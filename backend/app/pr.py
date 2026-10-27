"""Pull-request generation.

Dry run is the default and performs **no** write requests against GitHub. It
produces the complete title and Markdown body, so a reviewer can read exactly
what would be opened.

Real mode is gated on three things: a GitHub token, ``DRY_RUN=false``, and a
sandbox run whose post-fix tests passed. A failed or unreviewed fix can never
reach this code path.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from .config import get_settings
from .models import DependencyReport, FixPlan, FixType, PRPreview, SandboxResult, SandboxStatus

logger = logging.getLogger("ghost_maintainer.pr")

GITHUB_API = "https://api.github.com"
FOOTER = "Opened by Ghost Maintainer"

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "unknown": 4}


class PRError(Exception):
    """A user-facing explanation of why a pull request could not be opened."""


# ---------------------------------------------------------------------------
# content
# ---------------------------------------------------------------------------


def build_title(plan: FixPlan) -> str:
    """A conventional-commit style title."""
    if plan.type is FixType.VERSION_BUMP:
        return f"fix(deps): bump {plan.package} from {plan.from_version} to {plan.to_version}"
    if plan.type is FixType.PACKAGE_REPLACEMENT:
        return f"refactor(deps): replace {plan.package} with {plan.replacement}"
    return f"chore(deps): update {plan.package}"


def _rot_section(report: DependencyReport) -> list[str]:
    lines = [
        "## Rot Score",
        "",
        f"**{report.rot.value}/100 — {report.rot.label.value}**",
        "",
        "| Component | Points | Why |",
        "| --- | ---: | --- |",
    ]
    for component in report.rot.components:
        marker = "" if component.known else " (unknown)"
        lines.append(
            f"| {component.label} | {component.points}/{component.max_points}{marker} | {component.reason} |"
        )
    if not report.rot.reasons:
        lines.append("| — | 0 | No component contributed any points. |")
    return lines


def _vulnerability_section(report: DependencyReport) -> list[str]:
    if not report.vulnerabilities:
        return [
            "## Vulnerability evidence",
            "",
            "No known advisories affect the installed version at the time of the scan.",
        ]

    lines = [
        "## Vulnerability evidence",
        "",
        "| Advisory | Severity | Fixed in | Summary |",
        "| --- | --- | --- | --- |",
    ]
    for vulnerability in sorted(
        report.vulnerabilities, key=lambda item: _SEVERITY_ORDER.get(item.severity.value, 9)
    ):
        aliases = f" ({', '.join(vulnerability.aliases)})" if vulnerability.aliases else ""
        fixed = ", ".join(vulnerability.fixed_versions) or "not stated"
        summary = vulnerability.summary.replace("|", "\\|") or "—"
        lines.append(
            f"| {vulnerability.id}{aliases} | {vulnerability.severity.value} | {fixed} | {summary} |"
        )
    return lines


def _reachability_section(report: DependencyReport) -> list[str]:
    reachability = report.reachability
    lines = [
        "## Reachability",
        "",
        f"**{reachability.classification.value}** — {reachability.explanation}",
        "",
    ]
    if reachability.matches:
        lines.append("| File | Line | Reference |")
        lines.append("| --- | ---: | --- |")
        for match in reachability.matches[:15]:
            snippet = match.snippet.replace("|", "\\|")
            lines.append(f"| `{match.file}` | {match.line} | `{snippet}` |")
        if len(reachability.matches) > 15:
            lines.append(f"| … | | {len(reachability.matches) - 15} more reference(s) |")
        lines.append("")
    lines.append(f"> {reachability.limitation}")
    return lines


def _files_section(plan: FixPlan) -> list[str]:
    lines = ["## Files changed", ""]
    for path in plan.files_to_change:
        lines.append(f"- `{path}`")
    if not plan.files_to_change:
        lines.append("- No files were changed.")
    lines.append("")
    lines.append("```diff")
    for change in plan.changes:
        lines.append(change.diff.rstrip())
    lines.append("```")
    return lines


def _test_section(title: str, passed: bool | None, output: str, exit_code: int | None) -> list[str]:
    if passed is True:
        state = f"Passed (exit code {exit_code})"
    elif passed is False:
        state = f"Failed (exit code {exit_code})"
    else:
        state = "Not run"
    lines = [f"**{title}:** {state}"]
    if output:
        lines.append("")
        lines.append("```text")
        lines.append(output.strip()[-1500:])
        lines.append("```")
    return lines


def build_body(report: DependencyReport, plan: FixPlan, sandbox: SandboxResult) -> str:
    """The complete Markdown pull-request body."""
    lines: list[str] = []

    lines.append("## Dependency and version change")
    lines.append("")
    if plan.type is FixType.VERSION_BUMP:
        lines.append(f"- `{plan.package}`: `{plan.from_version}` → `{plan.to_version}`")
    else:
        lines.append(f"- `{plan.package}` → `{plan.replacement}`")
        if plan.to_version:
            lines.append(f"- Replacement version: `{plan.to_version}`")
    lines.append("")
    lines.append(plan.explanation)
    if plan.migration_notes:
        lines.append("")
        for note in plan.migration_notes:
            lines.append(f"- {note}")
    lines.append("")

    lines.extend(_rot_section(report))
    lines.append("")
    lines.extend(_vulnerability_section(report))
    lines.append("")
    lines.extend(_reachability_section(report))
    lines.append("")
    lines.extend(_files_section(plan))
    lines.append("")

    lines.append("## Verification")
    lines.append("")
    lines.append(f"- Sandbox mode: `{sandbox.mode}`")
    lines.append(f"- Command: `{sandbox.command}`")
    lines.append(f"- Duration: {sandbox.duration_seconds:.1f}s")
    if sandbox.notes:
        for note in sandbox.notes:
            lines.append(f"- {note}")
    lines.append("")
    lines.extend(_test_section("Baseline tests", sandbox.baseline_passed, sandbox.baseline_output_tail, sandbox.baseline_exit_code))
    lines.append("")
    lines.extend(_test_section("Post-fix tests", sandbox.after_passed, sandbox.test_output_tail, sandbox.after_exit_code))
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "Rot Score, reachability and vulnerability data are evidence gathered at scan time, not a "
        "guarantee. A package with no observed import may still be reached transitively, dynamically "
        "or through files this scanner does not parse."
    )
    lines.append("")
    lines.append(FOOTER)

    return "\n".join(lines).strip() + "\n"


def branch_name(package: str) -> str:
    """A deterministic, collision-resistant branch name."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    safe = "".join(char if char.isalnum() or char in "-._" else "-" for char in package)
    return f"ghost-maintainer/fix-{safe}-{stamp}"


def can_open_real_pr(plan: FixPlan, sandbox: SandboxResult) -> tuple[bool, str]:
    """Whether a real pull request may be opened."""
    settings = get_settings()
    if sandbox.status is not SandboxStatus.PASSED:
        return False, "The sandbox did not pass, so no pull request may be opened."
    if plan.requires_human_review:
        return False, "The fix requires human review before a pull request is appropriate."
    if settings.dry_run:
        return False, "DRY_RUN is enabled, so only a preview is produced."
    if not settings.github_configured:
        return False, "No GITHUB_TOKEN is configured, so a pull request cannot be created."
    return True, ""


# ---------------------------------------------------------------------------
# preview
# ---------------------------------------------------------------------------


def build_preview(
    report: DependencyReport,
    plan: FixPlan,
    sandbox: SandboxResult,
    *,
    repo_slug: str,
    base_branch: str = "main",
) -> PRPreview:
    """Build a dry-run preview. Performs no network requests."""
    allowed, reason = can_open_real_pr(plan, sandbox)
    notes: list[str] = []

    if sandbox.status is not SandboxStatus.PASSED:
        notes.append(f"Tests did not pass ({sandbox.status.value}); this preview is diagnostic only.")
    if plan.requires_human_review:
        notes.append("The fix requires human review and will not be opened automatically.")
    if not allowed and not notes:
        notes.append(reason)
    elif allowed:
        notes.append("Real pull-request mode is enabled; this preview can be opened.")

    notes.append(f"Target repository: {repo_slug}")

    return PRPreview(
        mode="dry_run",
        created=False,
        title=build_title(plan),
        body=build_body(report, plan, sandbox),
        branch=branch_name(plan.package),
        base_branch=base_branch,
        changed_files=plan.files_to_change,
        diff=sandbox.diff,
        url=None,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# real pull request
# ---------------------------------------------------------------------------


class GitHubClient:
    """A minimal GitHub REST client for branch, commit and pull-request creation."""

    def __init__(self, token: str | None = None, client: httpx.AsyncClient | None = None) -> None:
        settings = get_settings()
        self.token = token or settings.github_token
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> "GitHubClient":
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=GITHUB_API,
                timeout=httpx.Timeout(30.0),
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {self.token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "ghost-maintainer/0.1",
                },
            )
        if not self.token:
            raise PRError("No GITHUB_TOKEN is configured.")
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("GitHubClient must be used as an async context manager.")
        return self._client

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            response = await self.client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise PRError(f"Could not reach GitHub: {exc.__class__.__name__}.") from exc

        if response.status_code == 401:
            raise PRError("GitHub rejected the token (401). Check GITHUB_TOKEN.")
        if response.status_code == 403:
            detail = "rate limit" if "rate limit" in response.text.lower() else "insufficient permissions"
            raise PRError(f"GitHub refused the request (403): {detail}.")
        if response.status_code == 404:
            raise PRError(f"GitHub returned 404 for {path}. The repository may not exist or may be private.")
        if response.status_code >= 400:
            raise PRError(f"GitHub returned {response.status_code} for {path}: {response.text[:300]}")
        return response.json()

    async def repository(self, slug: str) -> dict:
        return await self._request("GET", f"/repos/{slug}")

    async def open_pull_request(
        self,
        *,
        slug: str,
        plan: FixPlan,
        title: str,
        body: str,
        branch: str,
        base_branch: str | None = None,
    ) -> tuple[str, str]:
        """Create a branch, commit the changed files and open a pull request."""
        repo = await self.repository(slug)

        if not (repo.get("permissions") or {}).get("push", False):
            raise PRError("The token does not have push permission on this repository.")

        base = base_branch or repo.get("default_branch") or "main"

        base_ref = await self._request("GET", f"/repos/{slug}/git/ref/heads/{base}")
        base_sha = base_ref["object"]["sha"]

        base_commit = await self._request("GET", f"/repos/{slug}/git/commits/{base_sha}")
        base_tree_sha = base_commit["tree"]["sha"]

        tree_entries = []
        for change in plan.changes:
            if not change.content:
                continue
            blob = await self._request(
                "POST",
                f"/repos/{slug}/git/blobs",
                json={"content": change.content, "encoding": "utf-8"},
            )
            tree_entries.append(
                {
                    "path": change.path,
                    "mode": "100644",
                    "type": "blob",
                    "sha": blob["sha"],
                }
            )

        if not tree_entries:
            raise PRError("The plan contains no file content to commit.")

        tree = await self._request(
            "POST",
            f"/repos/{slug}/git/trees",
            json={"base_tree": base_tree_sha, "tree": tree_entries},
        )

        commit = await self._request(
            "POST",
            f"/repos/{slug}/git/commits",
            json={
                "message": title,
                "tree": tree["sha"],
                "parents": [base_sha],
            },
        )

        await self._request(
            "POST",
            f"/repos/{slug}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": commit["sha"]},
        )

        pull = await self._request(
            "POST",
            f"/repos/{slug}/pulls",
            json={"title": title, "head": branch, "base": base, "body": body},
        )

        url = pull.get("html_url")
        if not isinstance(url, str) or not url:
            raise PRError("GitHub accepted the pull request but returned no URL.")
        return url, base


async def open_real_pr(
    report: DependencyReport,
    plan: FixPlan,
    sandbox: SandboxResult,
    *,
    repo_slug: str,
    client: httpx.AsyncClient | None = None,
) -> PRPreview:
    """Open a real pull request. Only call this after verifying it is permitted."""
    allowed, reason = can_open_real_pr(plan, sandbox)
    if not allowed:
        raise PRError(reason)

    title = build_title(plan)
    body = build_body(report, plan, sandbox)
    branch = branch_name(plan.package)

    async with GitHubClient(client=client) as github:
        url, base = await github.open_pull_request(
            slug=repo_slug,
            plan=plan,
            title=title,
            body=body,
            branch=branch,
        )

    return PRPreview(
        mode="real",
        created=True,
        title=title,
        body=body,
        branch=branch,
        base_branch=base,
        changed_files=plan.files_to_change,
        diff=sandbox.diff,
        url=url,
        notes=[f"Pull request created: {url}"],
    )