# release

[![pre-commit.ci status](https://results.pre-commit.ci/badge/github/conda-forge/release/main.svg)](https://results.pre-commit.ci/latest/github/conda-forge/release/main) [![tests](https://github.com/conda-forge/release/actions/workflows/tests.yml/badge.svg)](https://github.com/conda-forge/release/actions/workflows/tests.yml)

GitHub Action to update the version of a feedstock, with a GitLab CI/CD job template that does the same thing.

## Usage

Add a GitHub Actions workflow file like this one:

```yaml
name: update feedstock version
on:
  workflow_dispatch:
    inputs:
      version:
        description: 'the new version'
        required: true
        type: string

jobs:
  update-feedstock-version:
    runs-on: ubuntu-latest
    name: update-feedstock-version
    steps:
      - name: run
        uses: conda-forge/release@main
        with:
          feedstock: <name of feedstock>-feedstock
          version: ${{ inputs.version }}
          github-token: ${{ secrets.GITHUB_PAT }}
          automerge: true
```

Then you can trigger the version update by dispatching the workflow in the UI. It is also possible to trigger the workflow on GitHub release events.

See the [action.yml](action.yml) for details on possible inputs and options.

## Usage on GitLab CI/CD

GitLab cannot run GitHub Actions, so instead of the action you include a job template from this repository:

```yaml
stages:
  - deploy

include:
  - remote: https://raw.githubusercontent.com/conda-forge/release/v2026.9.15/gitlab/update-version.yml
    inputs:
      feedstock: <name of feedstock>-feedstock
      ref: v2026.9.15
      automerge: true
```

That adds an `update-feedstock-version` job which runs on tag pipelines and takes the new version from `$CI_COMMIT_TAG`, with any leading `v` stripped. Set the `version` input to override that, and the `rules` input to run the job at some other time.

Pin the URL and the `ref` input to the same tag. The URL selects the template, and `ref` selects the code that the template downloads and runs; `ref` defaults to `main`, which is only appropriate for testing.

The job reads the GitHub token from the `CF_RELEASE_GITHUB_TOKEN` CI/CD variable, and, if you use the dual token setup, the fork token from `CF_RELEASE_GITHUB_TOKEN_FOR_FORK`. The permissions required are the same as for the action.

See [gitlab/update-version.yml](gitlab/update-version.yml) for the full list of inputs.

### Self-hosted GitLab

`include: remote:` is fetched by the GitLab server rather than by the runner, so the server has to be able to reach `raw.githubusercontent.com`. If it cannot, copy the template into your own repository and use `include: local:` instead.

The job downloads this repository and the conda packages it needs when it runs. If the runner cannot reach github.com, set `CF_RELEASE_DIR` to a directory that already holds a checkout of this repository and the download is skipped.

The default `image` is `debian:stable-slim`, which the job installs `curl` and `micromamba` into. Override the `image` input to use a mirror of it, or any image that already provides `curl`, `tar`, `bzip2` and `micromamba`.

On GitLab 17.9 and later, add an [`integrity`](https://docs.gitlab.com/ci/yaml/#includeintegrity) hash so that the fetched template is verified before it is used:

```yaml
include:
  - remote: https://raw.githubusercontent.com/conda-forge/release/v2026.9.15/gitlab/update-version.yml
    integrity: sha256-<base64 of the sha256 digest>
    inputs:
      feedstock: <name of feedstock>-feedstock
      ref: v2026.9.15
```

## Required Token Permissions and Scopes

### Classic Tokens

For classic tokens, you need read/write permissions for the the `repo` and `workflow` scopes. For classic tokens, you pass the token to the `github-token` input.

### Fine-grained Tokens

For fine-grained tokens, you need to generate two tokens with different scopes and pass them to different inputs. You also need to have an existing fork of the target feedstock. The token persmissions are as follows:

| Action Input Parameter  | Allowed Repositories         | Repository Scopes (permissions)               |
| ----------------------- | ---------------------------- | --------------------------------------------- |
| `github-token`          | upstream feedstock           | pull_request (read/write)                     |
| `github-token-for-fork` | your fork of the feedstock   | contents (read/write), workflows (read/write) |

On GitLab the two tokens are read from the `CF_RELEASE_GITHUB_TOKEN` and `CF_RELEASE_GITHUB_TOKEN_FOR_FORK` CI/CD variables respectively.

## Protecting the Token

The token given to this action can open pull requests on the feedstock and, with `automerge: true`, add the `automerge` label to them. The conda-forge automerge service acts on that label regardless of who opened the pull request, so the token is better thought of as one that can land code in the package than one that can only propose changes.

Stored as a plain repository secret, it can be read by any workflow on any branch of the repository holding it. Storing it in a [GitHub environment](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments) instead, and naming that environment on the job, is a better default:

```yaml
jobs:
  update-feedstock-version:
    runs-on: ubuntu-latest
    name: update-feedstock-version
    environment: conda-forge
    steps:
      - name: run
        uses: conda-forge/release@main
        with:
          feedstock: <name of feedstock>-feedstock
          version: ${{ inputs.version }}
          github-token: ${{ secrets.GITHUB_PAT }}
          automerge: true
```

Give the environment a deployment branch policy listing the branches you release from, so that workflows on any other branch cannot read the secret. Required reviewers can be added as well, but note that the approval gates the whole job before it starts: a reviewer is approving the update being made at all, rather than reviewing the pull request it produces.

On GitLab, store the token as a [masked and protected](https://docs.gitlab.com/ci/variables/#mask-a-cicd-variable) CI/CD variable and release only from protected tags, so that a pipeline on an unprotected branch cannot read it. GitLab can mint OIDC ID tokens, but GitHub will not accept one in place of a personal access token, so a long-lived token is unavoidable here.

## Versioning and Deprecation Policy

This action follows [CalVer](https://calver.org/) with the format `YYYY.MM.DD`. Version tags are preceded by the letter `v`. The action's behavior, inputs, and outputs have a 60-day deprecation policy.
