"""GitHub I/O for the pull-request reviewer.

Reading a pull request's diff uses the public API and works without a token for
public repositories. Posting a review requires a token with `pull_requests: write`.

Posting is gated exactly like PR creation: dry run by default, and only inline
comments whose lines exist in the diff are sent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from .config import get_settings
from .review import Finding, ReviewReport, build_review_comment_body

logger = logging.getLogger("ghost_maintainer.review_github")

GITHUB_API = "https://api.github.com"

_SEVERITY_TO_GITHUB = {
    "critical": "critical",
}


class ReviewError(Exception):
    """A user-facing explanation of why a review could not run."""


@dataclass
class PullRequestInfo:
    slug: str
    number: int
    title: str
    base_branch: str
    head_sha: str
    patch: str
    author: str
    state: str


def _headers(token: str | None, *, diff: bool = False) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github.v3.diff" if diff else "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ghost-maintainer/0.1",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def fetch_pull_request(
    slug: str,
    number: int,
    *,
    token: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> PullRequestInfo:
    """Fetch a pull request's metadata and diff."""
    settings = get_settings()
    auth = token if token is not None else settings.github_token

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))

    try:
        meta_response = await client.get(
            f"{GITHUB_API}/repos/{slug}/pulls/{number}", headers=_headers(auth)
        )
        if meta_response.status_code == 404:
            raise ReviewError(
                f"Pull request {slug}#{number} was not found. It may be private, or the number may be wrong."
            )
        if meta_response.status_code == 403:
            raise ReviewError("GitHub refused the request (403). Add a GITHUB_TOKEN to raise the rate limit.")
        if meta_response.status_code >= 400:
            raise ReviewError(f"GitHub returned {meta_response.status_code} for {slug}#{number}.")

        meta = meta_response.json()

        diff_response = await client.get(
            f"{GITHUB_API}/repos/{slug}/pulls/{number}", headers=_headers(auth, diff=True)
        )
        if diff_response.status_code >= 400:
            raise ReviewError(f"Could not download the diff for {slug}#{number}.")

        return PullRequestInfo(
            slug=slug,
            number=number,
            title=str(meta.get("title") or ""),
            base_branch=str((meta.get("base") or {}).get("ref") or "main"),
            head_sha=str((meta.get("head") or {}).get("sha") or ""),
            patch=diff_response.text,
            author=str((meta.get("user") or {}).get("login") or ""),
            state=str(meta.get("state") or "open"),
        )
    except httpx.HTTPError as exc:
        raise ReviewError(f"Could not reach GitHub: {exc.__class__.__name__}.") from exc
    finally:
        if owns_client:
            await client.aclose()


def build_review_payload(report: ReviewReport, commit_sha: str) -> dict:
    """The JSON body for a pull-request review with inline comments."""
    comments = [
        {
            "path": finding.path,
            "line": finding.line,
            "side": "RIGHT",
            "body": _comment_body(finding),
        }
        for finding in report.findings[:40]
    ]
    body = {
        "commit_id": commit_sha,
        "body": build_review_comment_body(report),
        "event": "COMMENT",
        "comments": comments,
    }
    return body


def _comment_body(finding: Finding) -> str:
    lines = [f"**{finding.severity.value.upper()} · {finding.agent.value}**", "", finding.message]
    if finding.suggestion:
        lines.extend(["", f"_{finding.suggestion}_"])
    return "\n".join(lines)


async def post_review(
    report: ReviewReport,
    *,
    commit_sha: str,
    token: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> ReviewReport:
    """Post the review. In dry-run mode nothing is sent to GitHub."""
    settings = get_settings()
    if settings.dry_run or not settings.github_configured:
        report.posted = False
        report.dry_run = True
        report.notes.append("Dry run: the review was not posted to GitHub.")
        return report

    auth = token if token is not None else settings.github_token
    number = report.pull_number
    if number is None:
        raise ReviewError("Cannot post a review without a pull-request number.")

    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))

    try:
        response = await client.post(
            f"{GITHUB_API}/repos/{report.repo}/pulls/{number}/reviews",
            headers=_headers(auth),
            json=build_review_payload(report, commit_sha),
        )
        if response.status_code == 403:
            raise ReviewError("The token cannot post a review on this repository (403).")
        if response.status_code == 422:
            raise ReviewError(
                "GitHub rejected the review (422). A commented line was likely outside the diff."
            )
        if response.status_code >= 400:
            raise ReviewError(f"GitHub returned {response.status_code} while posting the review.")

        report.posted = True
        report.dry_run = False
        report.notes.append("Review posted with inline comments.")
        return report
    except httpx.HTTPError as exc:
        raise ReviewError(f"Could not reach GitHub: {exc.__class__.__name__}.") from exc
    finally:
        if owns_client:
            await client.aclose()