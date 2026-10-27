"""Scan and fix orchestration.

This is the seam between the pure analysis modules and the job/API layer. It owns
the ordering of the pipeline, the structured log messages the UI streams, and the
assembly of the typed responses.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

from . import __version__
from .config import get_settings
from .fixer import offer_fix_options, plan_fix
from .health import HealthService
from .models import (
    DepGraph,
    DependencyReport,
    FixRequest,
    FixResult,
    FixType,
    GraphNode,
    JobState,
    LogEntry,
    PRPreview,
    RiskLabel,
    ScanResult,
    ScanSummary,
    SandboxStatus,
)
from .pr import PRError, build_preview, can_open_real_pr, open_real_pr
from .reachability import analyse
from .sandbox import SandboxOptions, run_sandbox
from .scanner import ScannedRepository, cleanup_repository, parse_repo_ref, resolve_repository

logger = logging.getLogger("ghost_maintainer.pipeline")

LogSink = Callable[[str, str, str], None]
"""``(message, step, level)``"""


def _noop_log(message: str, step: str = "", level: str = "info") -> None:
    logger.info("[%s] %s", step or "pipeline", message)


async def _in_thread(function: Callable[..., object], *args: object, **kwargs: object) -> object:
    """Run blocking file/subprocess work off the event loop."""
    return await asyncio.to_thread(function, *args, **kwargs)


# ---------------------------------------------------------------------------
# graph and summary
# ---------------------------------------------------------------------------


def build_graph(repo_name: str, reports: list[DependencyReport]) -> DepGraph:
    """Build the dependency graph the dashboard renders."""
    project_id = "__project__"
    nodes = [
        GraphNode(
            id=project_id,
            label=repo_name,
            kind="project",
            rot=0,
            label_kind=RiskLabel.HEALTHY,
        )
    ]
    edges: list[dict[str, str]] = []

    for report in reports:
        node_id = f"pkg:{report.name}"
        nodes.append(
            GraphNode(
                id=node_id,
                label=report.name,
                kind="dependency",
                rot=report.rot.value,
                label_kind=report.rot.label,
                is_dev=report.is_dev,
                alerts=len(report.vulnerabilities),
                observed=report.reachability.classification.value == "observed",
            )
        )
        edges.append({"source": project_id, "target": node_id})

    return DepGraph(nodes=nodes, edges=edges)


def build_summary(reports: list[DependencyReport]) -> ScanSummary:
    """Counts shown in the dashboard summary cards."""
    summary = ScanSummary(total_dependencies=len(reports))

    for report in reports:
        if report.rot.label is RiskLabel.ROTTEN:
            summary.rotten += 1
        elif report.rot.label is RiskLabel.AGING:
            summary.aging += 1
        else:
            summary.healthy += 1

        alerts = len(report.vulnerabilities)
        summary.total_alerts += alerts
        if report.reachability.classification.value == "observed":
            summary.alerts_with_observed_usage += alerts

        if report.version:
            summary.resolved_versions += 1
        else:
            summary.unresolved_versions += 1

    return summary


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------


async def run_scan(
    repo_input: str,
    *,
    include_dev: bool = True,
    log: LogSink | None = None,
) -> tuple[ScanResult, ScannedRepository]:
    """Run the full scan pipeline and return the result plus its working copy."""
    emit = log or _noop_log
    settings = get_settings()
    started = datetime.now(timezone.utc)

    emit(f"Resolving {repo_input}", "resolve", "info")
    scanned = await _in_thread(resolve_repository, repo_input)  # type: ignore[arg-type]
    assert isinstance(scanned, ScannedRepository)

    for warning in scanned.warnings:
        emit(warning, "resolve", "warn")

    dependencies = scanned.dependencies
    if not include_dev:
        dependencies = [dep for dep in dependencies if not dep.is_dev]

    emit(
        f"Found {len(dependencies)} direct dependency/dependencies in {scanned.repo_name}",
        "manifest",
        "info",
    )

    resolved = sum(1 for dep in dependencies if dep.version_source == "lockfile")
    emit(f"Resolved {resolved} installed version(s) from the lockfile", "manifest", "info")

    emit("Querying npm, OSV, GitHub and deps.dev", "metadata", "info")
    async with HealthService() as health:
        enrichment = await health.enrich_all(dependencies, reference_time=started)

    for error in enrichment.errors:
        emit(error, "metadata", "warn")

    emit("Scanning source files for imports", "reachability", "info")
    vulnerabilities_by_package = {
        report.name: report.vulnerabilities for report in enrichment.reports
    }
    reachability = await _in_thread(
        analyse, scanned.root, dependencies, vulnerabilities_by_package
    )  # type: ignore[arg-type]

    reports: list[DependencyReport] = []
    for report in enrichment.reports:
        result = reachability.get(report.name) if isinstance(reachability, dict) else None
        if result is not None:
            report.reachability = result
        report.fix_options = await _in_thread(offer_fix_options, report, scanned.root)  # type: ignore[arg-type]
        reports.append(report)

    observed = sum(1 for report in reports if report.reachability.classification.value == "observed")
    emit(
        f"{observed} of {len(reports)} package(s) are referenced in source",
        "reachability",
        "info",
    )

    summary = build_summary(reports)
    graph = build_graph(scanned.repo_name, reports)

    repo_ref = parse_repo_ref(repo_input)
    slug = repo_ref.slug if repo_ref.kind == "github" else None

    result = ScanResult(
        scan_id="",
        repo_url=repo_input,
        repo_name=scanned.repo_name,
        repo_slug=slug,
        ref=scanned.ref,
        base_commit=scanned.base_commit,
        scanned_at=started,
        dependencies=reports,
        summary=summary,
        graph=graph,
        errors=list(scanned.warnings),
    )

    emit(
        f"Scan complete: {summary.rotten} rotten, {summary.aging} aging, "
        f"{summary.healthy} healthy, {summary.total_alerts} alert(s)",
        "complete",
        "success",
    )
    return result, scanned


# ---------------------------------------------------------------------------
# fix
# ---------------------------------------------------------------------------


def _find_report(scan: ScanResult, package: str) -> DependencyReport | None:
    for report in scan.dependencies:
        if report.name == package:
            return report
    return None


async def run_fix(
    scan: ScanResult,
    workspace: Path,
    request: FixRequest,
    *,
    log: LogSink | None = None,
) -> FixResult:
    """Plan, sandbox and (if allowed) prepare a pull request for one dependency."""
    emit = log or _noop_log
    settings = get_settings()

    result = FixResult(
        fix_id="",
        scan_id=scan.scan_id,
        package=request.package,
        state=JobState.RUNNING,
    )

    report = _find_report(scan, request.package)
    if report is None:
        raise ValueError(f"`{request.package}` is not part of this scan.")

    emit(f"Planning a fix for {request.package} {report.version or ''}".strip(), "plan", "info")

    fix_type = request.fix_type or _choose_fix_type(report, request.package)
    if fix_type is FixType.NONE:
        result.state = JobState.SUCCEEDED
        result.error = "No automatic fix is available for this package."
        emit(result.error, "plan", "warn")
        return result

    if request.fix_type is not None:
        available = {option.type for option in report.fix_options}
        if request.fix_type not in available:
            raise ValueError(
                f"`{request.fix_type.value}` is not an available fix for `{request.package}`."
            )
        emit(f"Using the requested fix type: {request.fix_type.value}", "plan", "info")

    plan = await _in_thread(plan_fix, report, workspace, fix_type)  # type: ignore[arg-type]
    result.plan = plan
    emit(plan.explanation, "plan", "info")

    if plan.requires_human_review and not plan.changes:
        emit("The fix needs human review, so nothing was modified.", "plan", "warn")

    if not plan.changes:
        result.state = JobState.SUCCEEDED
        result.error = "The plan produced no file changes, so nothing was executed."
        return result

    emit("Preparing an isolated working copy", "sandbox", "info")
    emit("Collecting baseline test results", "baseline", "info")

    options = SandboxOptions(
        timeout_seconds=settings.sandbox_timeout_seconds,
        mode=settings.sandbox_mode,
    )
    sandbox = await run_sandbox(plan, workspace, options=options)
    result.sandbox = sandbox

    if sandbox.status is SandboxStatus.BASELINE_FAILED:
        emit("The baseline test run failed; no fix was applied.", "baseline", "error")
    elif sandbox.status is SandboxStatus.TIMEOUT:
        emit("A sandbox command timed out and was terminated.", "sandbox", "error")
    else:
        emit(
            f"Baseline {'passed' if sandbox.baseline_passed else 'failed'}; "
            f"post-fix {'passed' if sandbox.after_passed else 'failed'}",
            "sandbox",
            "success" if sandbox.after_passed else "warn",
        )

    if sandbox.status is not SandboxStatus.PASSED:
        result.state = JobState.SUCCEEDED
        result.error = sandbox.error
        return result

    emit("Generating the unified diff", "diff", "info")
    emit("Preparing the pull-request preview", "pr", "info")

    repo_slug = scan.repo_slug or scan.repo_name
    base_branch = "main"

    allowed, _ = can_open_real_pr(plan, sandbox)
    if allowed:
        try:
            preview = await open_real_pr(report, plan, sandbox, repo_slug=repo_slug)
            emit(f"Opened pull request {preview.url}", "pr", "success")
        except PRError as exc:
            emit(f"Could not open the pull request: {exc}", "pr", "error")
            preview = build_preview(report, plan, sandbox, repo_slug=repo_slug, base_branch=base_branch)
            preview.notes.append(f"Real pull request failed: {exc}")
    else:
        preview = build_preview(report, plan, sandbox, repo_slug=repo_slug, base_branch=base_branch)
        emit("Dry-run preview prepared; no write requests were made to GitHub.", "pr", "info")

    result.pr = preview
    result.state = JobState.SUCCEEDED
    return result


def _choose_fix_type(report: DependencyReport, package: str) -> FixType:
    """Prefer a safe bump; fall back to a curated replacement."""
    from .fixer import analyse_bump, swap_for

    if report.vulnerabilities:
        analysis = analyse_bump(report)
        if analysis.target:
            return FixType.VERSION_BUMP

    if swap_for(package):
        return FixType.PACKAGE_REPLACEMENT

    if report.vulnerabilities:
        # Only a cross-major upgrade is left, which must not be applied silently.
        return FixType.VERSION_BUMP
    return FixType.NONE


async def run_fix_for_request(
    scan: ScanResult,
    workspace: Path,
    request: FixRequest,
    *,
    log: LogSink | None = None,
) -> FixResult:
    """Wrapper that always cleans up nothing and reports user errors as results."""
    try:
        return await run_fix(scan, workspace, request, log=log)
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI as a failed job
        emit = log or _noop_log
        emit(str(exc), "fix", "error")
        raise