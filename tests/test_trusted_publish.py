"""Run trusted_publish.sh against a local stand-in for conda-forge-webservices."""

import http.server
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "trusted_publish.sh"
TOKEN = "header.claims.signature"

# sh is dash on the GitHub runners, and the GitLab template runs it in busybox
SHELLS = [sh for sh in ("sh", "dash", "busybox") if shutil.which(sh)]


class _Endpoint(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers["Content-Length"])
        self.server.requests.append(
            {
                "path": self.path,
                "authorization": self.headers["Authorization"],
                "body": json.loads(self.rfile.read(length)),
            }
        )
        status, headers, body = self.server.answers.pop(0)
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, *args):
        pass


@pytest.fixture
def endpoint():
    server = http.server.HTTPServer(("127.0.0.1", 0), _Endpoint)
    server.requests = []
    server.answers = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()


@pytest.fixture(params=SHELLS)
def run(request, endpoint, tmp_path):
    def _run(*args, token=TOKEN):
        command = [request.param, str(SCRIPT), *args]
        if request.param == "busybox":
            command.insert(1, "sh")
        env = dict(
            os.environ,
            CF_RELEASE_WEBSERVICES=f"http://127.0.0.1:{endpoint.server_port}",
            CF_RELEASE_OUTPUT_FILE=str(tmp_path / "output"),
        )
        env.pop("CF_RELEASE_ID_TOKEN", None)
        if token is not None:
            env["CF_RELEASE_ID_TOKEN"] = token
        return subprocess.run(
            command, env=env, capture_output=True, text=True, check=False
        )

    return _run


OPENED = (202, {}, {"pull_request": 12, "url": "https://github.com/x/y/pull/12"})


def test_a_version_update_is_asked_for(run, endpoint, tmp_path):
    endpoint.answers = [OPENED]
    result = run("lbenv-feedstock", "2.4.0")

    assert result.returncode == 0, result.stderr
    assert "https://github.com/x/y/pull/12" in result.stdout
    assert endpoint.requests == [
        {
            "path": "/trusted-publishing/version-update",
            "authorization": f"Bearer {TOKEN}",
            "body": {"feedstock": "lbenv-feedstock", "version": "2.4.0"},
        }
    ]
    assert (tmp_path / "output").read_text() == "pull-request-number=12\n"


def test_a_version_update_that_did_not_start_fails_the_job(run, endpoint, tmp_path):
    endpoint.answers = [
        (
            202,
            {},
            {
                "pull_request": 12,
                "url": "https://github.com/x/y/pull/12",
                "version_update_started": False,
            },
        )
    ]
    result = run("lbenv-feedstock", "2.4.0")

    assert result.returncode == 1
    assert "could not start" in result.stderr
    assert "run this job again" in result.stderr
    # the pull request was still opened, so the action still says which
    assert (tmp_path / "output").read_text() == "pull-request-number=12\n"
    assert len(endpoint.requests) == 1


def test_a_feedstock_branch_is_passed_on(run, endpoint):
    endpoint.answers = [OPENED]
    assert run("lbenv-feedstock", "2.4.0", "2.4.x").returncode == 0
    assert endpoint.requests[0]["body"]["branch"] == "2.4.x"


def test_a_retryable_refusal_is_retried_with_the_same_token(run, endpoint):
    endpoint.answers = [
        (503, {"Retry-After": "0"}, {"error": "keys not loaded yet"}),
        OPENED,
    ]
    result = run("lbenv-feedstock", "2.4.0")

    assert result.returncode == 0, result.stderr
    assert [r["authorization"] for r in endpoint.requests] == [f"Bearer {TOKEN}"] * 2


def test_a_refusal_fails_the_job_and_says_why(run, endpoint, tmp_path):
    endpoint.answers = [(403, {}, {"error": "the token matches no trusted publisher"})]
    result = run("lbenv-feedstock", "2.4.0")

    assert result.returncode == 1
    assert "HTTP 403" in result.stderr
    assert "matches no trusted publisher" in result.stderr
    assert not (tmp_path / "output").exists()


def test_a_missing_token_is_refused_before_asking(run, endpoint):
    result = run("lbenv-feedstock", "2.4.0", token=None)
    assert result.returncode == 1
    assert "CF_RELEASE_ID_TOKEN" in result.stderr
    assert endpoint.requests == []


@pytest.mark.parametrize(
    "args",
    [
        ["lbenv", "2.4.0"],
        ["lbenv-feedstock", "v2.4.0-rc1"],
        ["lbenv-feedstock", '2.4.0", "feedstock": "other-feedstock'],
        ["lbenv-feedstock", "2.4.0\nother"],
        ["lbenv-feedstock", ""],
        ["lbenv-feedstock", "2.4.0", "../main"],
        ["lbenv-feedstock", "2.4.0", 'x", "feedstock": "y'],
    ],
)
def test_values_the_endpoint_would_refuse_are_refused_before_asking(
    run, endpoint, args
):
    """They go into the json body as they are, so none may hold a quote."""
    result = run(*args)
    assert result.returncode == 1
    assert "not one conda-forge will accept" in result.stderr
    assert endpoint.requests == []
