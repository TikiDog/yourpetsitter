"""Delivers the digest as a GitHub issue assigned to the repo owner.

Used when no email (SMTP) login is configured. GitHub emails the assignee,
so this works with only the token GitHub Actions provides automatically.
Yesterday's digest issue is closed when today's opens.
"""

from __future__ import annotations

import os
import urllib.parse

from . import http

LABEL = "job-digest"
MAX_BODY = 60000  # GitHub's limit is 65,536 characters


def available() -> bool:
    return bool(os.environ.get("GITHUB_TOKEN") and os.environ.get("GITHUB_REPOSITORY"))


def post(title: str, body: str) -> str:
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    repo = os.environ["GITHUB_REPOSITORY"]
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER") or repo.split("/")[0]
    headers = {"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
               "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}

    old = http.get_json(f"{api}/repos/{repo}/issues",
                        {"labels": LABEL, "state": "open", "per_page": 100}, headers)

    if len(body) > MAX_BODY:
        body = body[:MAX_BODY].rsplit("\n", 1)[0] + "\n\n_…list cut short (too long for one issue)._"
    issue = http.send_json("POST", f"{api}/repos/{repo}/issues",
                           {"title": title, "body": body, "labels": [LABEL],
                            "assignees": [owner]}, headers)

    for o in old:
        if "pull_request" not in o:
            http.send_json("PATCH", f"{api}/repos/{repo}/issues/{o['number']}",
                           {"state": "closed", "state_reason": "completed"}, headers)
    return issue.get("html_url", "")
