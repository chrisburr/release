import argparse
import base64
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

LOGGER = logging.getLogger(__name__)

ORG = "conda-forge"
DEFAULT_BRANCH_PREFIX = "update-feedstock-version"


def _git(*args, auth_token=None, **kwargs):
    """Run git, optionally authenticating with a GitHub token.

    The token is passed via an ``AUTHORIZATION`` header rather than being
    embedded in the remote URL so that git cannot echo it back in an error
    message.
    """
    cmd = ["git"]
    if auth_token is not None:
        basic = base64.b64encode(f"x-access-token:{auth_token}".encode()).decode()
        cmd += ["-c", f"http.extraheader=AUTHORIZATION: basic {basic}"]
    cmd += [str(arg) for arg in args]
    return subprocess.run(cmd, check=True, text=True, **kwargs)


def _git_stdout(*args, **kwargs):
    return _git(*args, stdout=subprocess.PIPE, **kwargs).stdout.strip()


def _github_client(token):
    from github import Auth, Github

    return Github(auth=Auth.Token(token))


def _git_user(user):
    """The git identity to use for the commit, taken from the token's owner."""
    email = user.email or f"{user.id}+{user.login}@users.noreply.github.com"
    return user.login, email


def _default_branch_name(login, feedstock_branch):
    name = f"{login}/{DEFAULT_BRANCH_PREFIX}"
    if feedstock_branch:
        name += f"/{feedstock_branch}"
    return name


def _write_output(output_file, **outputs):
    """Append ``key=value`` lines, the format GitHub Actions uses for $GITHUB_OUTPUT."""
    for key, value in outputs.items():
        LOGGER.info("output %s=%s", key, value)
    if output_file:
        with open(output_file, "a") as fp:
            for key, value in outputs.items():
                fp.write(f"{key}={value}\n")


def clone(args):
    url = f"https://github.com/{ORG}/{args.feedstock}.git"
    cmd = ["clone", "--depth", "1"]
    if args.feedstock_branch:
        cmd += ["--branch", args.feedstock_branch]
    _git(*cmd, url, args.feedstock, auth_token=os.environ["GH_TOKEN"])


def update_version(args):
    from conda_forge_feedstock_ops.update_build_number import update_build_number
    from conda_forge_feedstock_ops.update_version import (
        update_version as cf_feedstock_ops_update_version,
    )

    try:
        updated, errors = cf_feedstock_ops_update_version(
            args.feedstock,
            str(args.version),
            use_container=False,
        )
        if errors or (not updated):
            raise RuntimeError(f"Error updating the recipe version:\n{errors!r}")
    except Exception:
        LOGGER.exception("error while updating the recipe version!")
        sys.exit(1)

    try:
        workdir = Path(args.feedstock)
        meta_yaml_path = workdir.joinpath("recipe", "meta.yaml")
        recipe_yaml_path = workdir.joinpath("recipe", "recipe.yaml")
        if meta_yaml_path.exists():
            update_build_number(
                meta_yaml_path,
                0,
            )
        elif recipe_yaml_path.exists():
            update_build_number(
                recipe_yaml_path,
                0,
            )
        else:
            raise FileNotFoundError("Could not find meta.yaml or recipe.yaml!")
    except Exception:
        LOGGER.exception("error while resetting the recipe build number!")
        sys.exit(1)


def rerender(args):
    from conda_forge_feedstock_ops.rerender import rerender as cf_feedstock_ops_rerender

    try:
        msg = cf_feedstock_ops_rerender(
            args.feedstock,
            timeout=None,
            use_container=False,
        )
    except Exception:
        LOGGER.exception("error while rerendering!")
        sys.exit(1)

    commit_message = f"chore: update version to {args.version}"
    if msg is not None:
        commit_message += " & " + msg[len("chore: ") :]

    LOGGER.info("commit message: %s", commit_message)
    if args.commit_message_file:
        Path(args.commit_message_file).write_text(commit_message)


def open_pr(args):
    from github import GithubException

    gh = _github_client(os.environ["GH_TOKEN"])
    fork_token = os.environ.get("GH_TOKEN_FOR_FORK") or os.environ["GH_TOKEN"]

    login, email = _git_user(gh.get_user())
    branch = args.branch or _default_branch_name(login, args.feedstock_branch)
    fork_url = f"https://github.com/{login}/{args.feedstock}.git"

    upstream = gh.get_repo(f"{ORG}/{args.feedstock}")
    base = args.feedstock_branch or upstream.default_branch

    if args.commit_message_file and Path(args.commit_message_file).exists():
        commit_message = Path(args.commit_message_file).read_text().strip()
    else:
        commit_message = f"chore: update version to {args.version}"

    _git("add", "--all", cwd=args.feedstock)
    if not _git_stdout("status", "--porcelain", cwd=args.feedstock):
        LOGGER.info("no changes to commit - not opening a pull request")
        _delete_branch(fork_url, branch, fork_token, cwd=args.feedstock)
        _write_output(args.output_file, **{"pull-request-number": ""})
        return

    if os.environ.get("GH_TOKEN_FOR_FORK"):
        # a fine-grained fork token cannot create the fork, so it must already exist
        LOGGER.info("assuming the fork %s/%s already exists", login, args.feedstock)
    else:
        _ensure_fork(gh, upstream, f"{login}/{args.feedstock}")

    _git(
        "-c",
        f"user.name={login}",
        "-c",
        f"user.email={email}",
        "commit",
        "-m",
        commit_message,
        cwd=args.feedstock,
    )
    _git(
        "push",
        "--force",
        fork_url,
        f"HEAD:refs/heads/{branch}",
        auth_token=fork_token,
        cwd=args.feedstock,
    )

    head = f"{login}:{branch}"
    existing = list(upstream.get_pulls(state="open", head=head, base=base))
    if existing:
        pr = existing[0]
        LOGGER.info("updated the existing pull request %s", pr.html_url)
    else:
        pr = upstream.create_pull(
            title=f"chore: update version to {args.version}",
            body=f"This pull request updates the version to {args.version}.",
            head=head,
            base=base,
            maintainer_can_modify=True,
        )
        LOGGER.info("opened the pull request %s", pr.html_url)

    if args.automerge == "true":
        try:
            pr.add_to_labels("automerge")
        except GithubException:
            LOGGER.exception("could not add the automerge label!")
            sys.exit(1)

    _write_output(args.output_file, **{"pull-request-number": pr.number})


def _ensure_fork(gh, upstream, fork_name, timeout=300):
    """Fork the feedstock, waiting for GitHub to finish creating it if it is new."""
    from github import UnknownObjectException

    upstream.create_fork()

    deadline = time.monotonic() + timeout
    while True:
        try:
            gh.get_repo(fork_name)
            return
        except UnknownObjectException:
            if time.monotonic() > deadline:
                raise
            LOGGER.info("waiting for the fork %s to appear", fork_name)
            time.sleep(5)


def _delete_branch(fork_url, branch, token, cwd):
    """Remove a stale branch left over from a previous run that now has no changes."""
    refs = _git_stdout(
        "ls-remote", "--heads", fork_url, branch, auth_token=token, cwd=cwd
    )
    if refs:
        _git("push", "--delete", fork_url, branch, auth_token=token, cwd=cwd)
        LOGGER.info("deleted the stale branch %s", branch)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="task", required=True)

    def _add(name, func, *, version=True):
        sub = subparsers.add_parser(name)
        sub.add_argument(
            "feedstock", help='the feedstock name (e.g. "python-feedstock")'
        )
        if version:
            sub.add_argument("version", help="the new version")
        sub.set_defaults(func=func)
        return sub

    sub = _add("clone", clone, version=False)
    sub.add_argument("--feedstock-branch", default="")

    _add("update-version", update_version)

    sub = _add("rerender", rerender)
    sub.add_argument("--commit-message-file", default="")

    sub = _add("open-pr", open_pr)
    sub.add_argument("--branch", default="")
    sub.add_argument("--feedstock-branch", default="")
    sub.add_argument("--commit-message-file", default="")
    sub.add_argument("--output-file", default="")
    sub.add_argument("--automerge", default="false", choices=["true", "false"])

    args = parser.parse_args()

    logging.basicConfig(
        format="%(asctime)-15s %(levelname)-8s %(name)s@%(filename)s:%(lineno)d || %(message)s",
        level=logging.INFO,
    )

    args.func(args)


if __name__ == "__main__":
    main()
