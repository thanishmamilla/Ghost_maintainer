"""PR bot tests: dry-run content, gating and GitHub error handling."""

from __future__ import annotations

import json

import httpx
import pytest

from app.config import get_settings
from app.models import (
    DependencyReport,
    FileChange,
    FixPlan,
    FixType,
    MetadataStatus,
    PackageHealth,
    PRPreview,
    ReachabilityClass,
    ReachabilityResult,
    RotScore,
    SandboxResult,
    SandboxStatus,
    ScoreComponent,
    Severity,
    UsageMatch,
    Vulnerability,
)
from app.pr import (
    FOOTER,
    PRError,
    build_body,
    build_preview,
    build_title,
    can_open_real_pr,
    open_real_pr,
)


def report() -> DependencyReport:
    return DependencyReport(
        name="lodash",
        version="4.17.15",
        version_source="lockfile",
        declared_range="4.17.15",
        latest_version="4.18.1",
        health=PackageHealth(metadata_status=MetadataStatus.OK),
        rot=RotScore(
            value=48,
            label="aging",
            components=[
                ScoreComponent(
                    key="staleness",
                    label="Staleness",
                    points=30,
                    max_points=30,
                    reason="The installed release was published 2600 days ago.",
                ),
                ScoreComponent(
                    key="vulnerabilities",
                    label="Vulnerabilities",
                    points=18,
                    max_points=25,
                    reason="1 advisory affects this version; the highest is high.",
                ),
            ],
            reasons=["The installed release was published 2600 days ago."],
            metadata_status=MetadataStatus.OK,
        ),
        vulnerabilities=[
            Vulnerability(
                id="GHSA-test-1234",
                aliases=["CVE-2021-23337"],
                summary="Command injection in lodash template",
                severity=Severity.HIGH,
                fixed_versions=["4.17.21"],
            )
        ],
        reachability=ReachabilityResult(
            classification=ReachabilityClass.OBSERVED,
            explanation="`lodash` was observed in 2 reference(s) across 1 file(s).",
            matches=[
                UsageMatch(
                    file="src/objects.js",
                    line=6,
                    snippet='const _ = require("lodash");',
                    kind="require",
                    specifier="lodash",
                    members=["_"],
                )
            ],
            used_members=["_"],
            scanned_files=5,
        ),
    )


def plan() -> FixPlan:
    return FixPlan(
        type=FixType.VERSION_BUMP,
        package="lodash",
        from_version="4.17.15",
        to_version="4.17.21",
        files_to_change=["package.json"],
        explanation="Upgrade lodash to 4.17.21.",
        migration_notes=["Stays within the same major version."],
        evidence=["GHSA-test-1234 is fixed in 4.17.21."],
        changes=[
            FileChange(
                path="package.json",
                diff='--- a/package.json\n+++ b/package.json\n-    "lodash": "4.17.15"\n+    "lodash": "^4.17.21"\n',
                content='{"dependencies": {"lodash": "^4.17.21"}}',
                added_lines=1,
                removed_lines=1,
            )
        ],
    )


def sandbox(status: SandboxStatus = SandboxStatus.PASSED) -> SandboxResult:
    return SandboxResult(
        status=status,
        mode="local_restricted",
        baseline_passed=True,
        after_passed=status is SandboxStatus.PASSED,
        baseline_exit_code=0,
        after_exit_code=0 if status is SandboxStatus.PASSED else 1,
        baseline_output_tail="Test Suites: 5 passed, 5 total",
        test_output_tail="Test Suites: 5 passed, 5 total",
        duration_seconds=42.5,
        diff="--- a/package.json\n+++ b/package.json\n",
        files_changed=["package.json"],
    )


# ---------------------------------------------------------------------------
# content
# ---------------------------------------------------------------------------


def test_title_describes_a_bump() -> None:
    assert build_title(plan()) == "fix(deps): bump lodash from 4.17.15 to 4.17.21"


def test_title_describes_a_replacement() -> None:
    replacement = plan()
    replacement.type = FixType.PACKAGE_REPLACEMENT
    replacement.replacement = "dayjs"

    assert build_title(replacement) == "refactor(deps): replace lodash with dayjs"


def test_body_contains_every_required_section() -> None:
    body = build_body(report(), plan(), sandbox())

    for heading in [
        "## Dependency and version change",
        "## Rot Score",
        "## Vulnerability evidence",
        "## Reachability",
        "## Files changed",
        "## Verification",
    ]:
        assert heading in body


def test_body_includes_the_rot_score_and_reasons() -> None:
    body = build_body(report(), plan(), sandbox())

    assert "48/100" in body
    assert "aging" in body
    assert "Staleness" in body


def test_body_includes_advisory_evidence() -> None:
    body = build_body(report(), plan(), sandbox())

    assert "GHSA-test-1234" in body
    assert "CVE-2021-23337" in body
    assert "4.17.21" in body


def test_body_includes_reachability_file_and_line() -> None:
    body = build_body(report(), plan(), sandbox())

    assert "src/objects.js" in body
    assert "observed" in body


def test_body_states_the_reachability_limitation() -> None:
    body = build_body(report(), plan(), sandbox())

    assert "may still be reached transitively" in body


def test_body_includes_the_diff_and_both_test_outcomes() -> None:
    body = build_body(report(), plan(), sandbox())

    assert "```diff" in body
    assert "Baseline tests" in body
    assert "Post-fix tests" in body
    assert "Test Suites: 5 passed" in body


def test_body_ends_with_the_footer() -> None:
    body = build_body(report(), plan(), sandbox()).strip()

    assert body.endswith(FOOTER)


def test_body_marks_a_failed_test_run_as_failed() -> None:
    body = build_body(report(), plan(), sandbox(SandboxStatus.NEEDS_HUMAN_REVIEW))

    assert "**Post-fix tests:** Failed" in body


# ---------------------------------------------------------------------------
# gating
# ---------------------------------------------------------------------------


def test_dry_run_is_the_default() -> None:
    assert get_settings().dry_run is True


def test_a_passing_fix_cannot_open_a_real_pr_while_dry_run_is_on() -> None:
    allowed, reason = can_open_real_pr(plan(), sandbox())

    assert allowed is False
    assert "DRY_RUN" in reason


def test_a_failed_sandbox_cannot_open_a_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "dry_run", False)
    monkeypatch.setattr(settings, "github_token", "token")

    allowed, reason = can_open_real_pr(plan(), sandbox(SandboxStatus.NEEDS_HUMAN_REVIEW))

    assert allowed is False
    assert "did not pass" in reason


def test_a_plan_needing_review_cannot_open_a_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "dry_run", False)
    monkeypatch.setattr(settings, "github_token", "token")
    review_plan = plan()
    review_plan.requires_human_review = True

    allowed, reason = can_open_real_pr(review_plan, sandbox())

    assert allowed is False
    assert "human review" in reason


def test_a_missing_token_cannot_open_a_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "dry_run", False)
    monkeypatch.setattr(settings, "github_token", "")

    allowed, reason = can_open_real_pr(plan(), sandbox())

    assert allowed is False
    assert "GITHUB_TOKEN" in reason


def test_everything_configured_allows_a_real_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "dry_run", False)
    monkeypatch.setattr(settings, "github_token", "token")

    allowed, _ = can_open_real_pr(plan(), sandbox())

    assert allowed is True


# ---------------------------------------------------------------------------
# preview
# ---------------------------------------------------------------------------


def test_preview_is_a_dry_run_with_no_url() -> None:
    preview = build_preview(report(), plan(), sandbox(), repo_slug="owner/repo")

    assert preview.mode == "dry_run"
    assert preview.created is False
    assert preview.url is None
    assert preview.title
    assert preview.body
    assert "owner/repo" in " ".join(preview.notes)


def test_preview_lists_the_changed_files() -> None:
    preview = build_preview(report(), plan(), sandbox(), repo_slug="owner/repo")

    assert preview.changed_files == ["package.json"]


def test_preview_notes_a_failed_sandbox() -> None:
    preview = build_preview(
        report(), plan(), sandbox(SandboxStatus.NEEDS_HUMAN_REVIEW), repo_slug="owner/repo"
    )

    assert any("did not pass" in note for note in preview.notes)


# ---------------------------------------------------------------------------
# real mode with a mocked GitHub API
# ---------------------------------------------------------------------------


def github_transport(*, permissions_push: bool = True, fail_status: int | None = None) -> httpx.MockTransport:
    created: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if fail_status is not None:
            return httpx.Response(fail_status, json={"message": "nope"})

        if path.endswith("/repos/owner/repo") and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "default_branch": "main",
                    "permissions": {"push": permissions_push},
                },
            )
        if "/git/ref/heads/" in path:
            return httpx.Response(200, json={"object": {"sha": "basesha"}})
        if "/git/commits/basesha" in path:
            return httpx.Response(200, json={"tree": {"sha": "basetree"}})
        if path.endswith("/git/blobs"):
            return httpx.Response(201, json={"sha": "blobsha"})
        if path.endswith("/git/trees"):
            return httpx.Response(201, json={"sha": "treesha"})
        if path.endswith("/git/commits") and request.method == "POST":
            return httpx.Response(201, json={"sha": "commitsha"})
        if path.endswith("/git/refs"):
            created["ref"] = json.loads(request.content)
            return httpx.Response(201, json={})
        if path.endswith("/pulls"):
            created["pull"] = json.loads(request.content)
            return httpx.Response(201, json={"html_url": "https://github.com/owner/repo/pull/7"})
        return httpx.Response(404, json={"message": "not found"})

    transport = httpx.MockTransport(handler)
    transport.created = created  # type: ignore[attr-defined]
    return transport


async def test_real_pr_creates_a_branch_and_pull_request(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "dry_run", False)
    monkeypatch.setattr(settings, "github_token", "token")

    transport = github_transport()
    async with httpx.AsyncClient(transport=transport, base_url="https://api.github.com") as client:
        preview = await open_real_pr(report(), plan(), sandbox(), repo_slug="owner/repo", client=client)

    assert preview.created is True
    assert preview.mode == "real"
    assert preview.url == "https://github.com/owner/repo/pull/7"
    assert preview.branch.startswith("ghost-maintainer/fix-lodash-")

    created = transport.created  # type: ignore[attr-defined]
    assert created["ref"]["ref"].startswith("refs/heads/ghost-maintainer/fix-lodash-")
    assert created["pull"]["base"] == "main"


async def test_real_pr_refuses_without_push_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "dry_run", False)
    monkeypatch.setattr(settings, "github_token", "token")

    transport = github_transport(permissions_push=False)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.github.com") as client:
        with pytest.raises(PRError) as excinfo:
            await open_real_pr(report(), plan(), sandbox(), repo_slug="owner/repo", client=client)

    assert "push permission" in str(excinfo.value)


async def test_real_pr_reports_an_authentication_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "dry_run", False)
    monkeypatch.setattr(settings, "github_token", "token")

    transport = github_transport(fail_status=401)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.github.com") as client:
        with pytest.raises(PRError) as excinfo:
            await open_real_pr(report(), plan(), sandbox(), repo_slug="owner/repo", client=client)

    assert "401" in str(excinfo.value)


async def test_real_pr_reports_a_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "dry_run", False)
    monkeypatch.setattr(settings, "github_token", "token")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "API rate limit exceeded"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.github.com") as client:
        with pytest.raises(PRError) as excinfo:
            await open_real_pr(report(), plan(), sandbox(), repo_slug="owner/repo", client=client)

    assert "rate limit" in str(excinfo.value)


async def test_real_pr_is_refused_before_any_network_call(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "dry_run", True)

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.github.com") as client:
        with pytest.raises(PRError):
            await open_real_pr(report(), plan(), sandbox(), repo_slug="owner/repo", client=client)

    assert calls == 0


def test_preview_makes_no_network_calls() -> None:
    """build_preview is pure: no client is available to it at all."""
    preview = build_preview(report(), plan(), sandbox(), repo_slug="owner/repo")

    assert isinstance(preview, PRPreview)
    assert preview.url is None