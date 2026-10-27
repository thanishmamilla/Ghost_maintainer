"""HTTP API: scan, fix, streaming logs and the AI review surface."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from .config import get_settings
from .jobs import Job, manager
from .models import (
    FixJobResponse,
    FixRequest,
    FixResult,
    JobAccepted,
    JobState,
    ReviewJobResponse,
    ReviewRequest,
    ScanJobResponse,
    ScanRequest,
    ScanResult,
    WebhookDelivery,
)
from .pipeline import run_fix, run_scan
from .scanner import cleanup_repository

logger = logging.getLogger("ghost_maintainer.api")

router = APIRouter(prefix="/api", tags=["maintainer"])

# A conservative npm package-name shape. This is a security boundary: the name is
# used to look up a dependency and to build file paths, so anything that could
# traverse a directory or invoke a shell is rejected here.
_PACKAGE_NAME_RE = re.compile(r"^(?:@[a-z0-9][a-z0-9._~-]*/)?[a-z0-9][a-z0-9._~-]*$")
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")


def _validate_package(name: str) -> str:
    candidate = (name or "").strip()
    if not _PACKAGE_NAME_RE.match(candidate):
        raise HTTPException(
            status_code=422,
            detail="That is not a valid npm package name.",
        )
    return candidate


def _validate_job_id(value: str) -> str:
    if not _JOB_ID_RE.match(value or ""):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
    return value


def _scan_job(job_id: str) -> Job:
    try:
        return manager.require(_validate_job_id(job_id), "scan")
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scan job not found.") from None


def _fix_job(job_id: str) -> Job:
    try:
        return manager.require(_validate_job_id(job_id), "fix")
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fix job not found.") from None


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------


@router.post("/scan", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED)
async def start_scan(request: ScanRequest) -> JobAccepted:
    """Start a scan. Returns a job id to poll."""
    job = manager.create("scan", repo_url=request.repo_url, include_dev=request.include_dev)

    async def worker(current: Job) -> ScanResult:
        result, scanned = await run_scan(
            request.repo_url,
            include_dev=request.include_dev,
            log=lambda message, step="", level="info": current.log(message, step, level),
        )
        result.scan_id = current.id
        # Keep the working copy alive so a fix does not have to clone again.
        manager.store[f"scan:{current.id}"] = scanned
        current.progress = 100
        return result

    manager.start(job, worker)
    return JobAccepted(job_id=job.id)


@router.get("/scan/{job_id}", response_model=ScanJobResponse)
async def scan_status(job_id: str) -> ScanJobResponse:
    job = _scan_job(job_id)
    return ScanJobResponse(
        job_id=job.id,
        state=job.state,
        progress=job.progress,
        error=job.error,
        result=job.result if isinstance(job.result, ScanResult) else None,
        logs=job.logs,
    )


# ---------------------------------------------------------------------------
# fix
# ---------------------------------------------------------------------------


@router.post("/fix", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED)
async def start_fix(request: FixRequest) -> JobAccepted:
    """Start a fix for one dependency of a completed scan."""
    scan_job = _scan_job(request.scan_id)
    if scan_job.state is not JobState.SUCCEEDED or not isinstance(scan_job.result, ScanResult):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The scan has not finished successfully yet.",
        )

    scan: ScanResult = scan_job.result
    package = _validate_package(request.package)
    if not any(dependency.name == package for dependency in scan.dependencies):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"`{package}` is not part of this scan.",
        )

    scanned = manager.store.get(f"scan:{scan_job.id}")
    if scanned is None or not Path(getattr(scanned, "root", "")).is_dir():
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="The working copy for this scan is no longer available. Run the scan again.",
        )

    job = manager.create("fix", scan_id=scan.scan_id, package=package)

    async def worker(current: Job) -> FixResult:
        result = await run_fix(
            scan,
            Path(scanned.root),
            request,
            log=lambda message, step="", level="info": current.log(message, step, level),
        )
        result.fix_id = current.id
        # Mirror the job log onto the result so both access paths agree.
        result.logs = list(current.logs)
        current.progress = 100
        return result

    manager.start(job, worker)
    return JobAccepted(job_id=job.id)


@router.get("/fix/{job_id}", response_model=FixJobResponse)
async def fix_status(job_id: str) -> FixJobResponse:
    job = _fix_job(job_id)
    return FixJobResponse(
        job_id=job.id,
        state=job.state,
        progress=job.progress,
        error=job.error,
        result=job.result if isinstance(job.result, FixResult) else None,
        logs=job.logs,
    )


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"


@router.get("/fix/{job_id}/stream")
async def stream_fix(job_id: str, request: Request) -> StreamingResponse:
    """Server-sent events for a fix job, with heartbeats and a terminal event."""
    job = _fix_job(job_id)

    async def generator() -> AsyncIterator[str]:
        queue = manager.subscribe(job)

        # Replay what already happened, so a late subscriber is not missing context.
        for entry in job.logs:
            yield _sse("log", {"entry": entry.model_dump(mode="json"), "state": job.state.value})
        yield _sse(
            "state",
            {"state": job.state.value, "progress": job.progress, "error": job.error},
        )

        if job.finished.is_set():
            yield _sse("done", {"state": job.state.value, "error": job.error})
            manager.unsubscribe(job, queue)
            return

        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
                    continue

                yield _sse(event["type"], event)
                if event["type"] == "state" and event.get("state") in {"succeeded", "failed"}:
                    yield _sse("done", {"state": event.get("state"), "error": event.get("error")})
                    break
        finally:
            manager.unsubscribe(job, queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------


def cleanup_scan(scan_id: str) -> None:
    """Remove the working copy for a scan, if it was a temporary clone."""
    scanned = manager.store.pop(f"scan:{scan_id}", None)
    if scanned is not None:
        cleanup_repository(scanned)


@router.delete("/scan/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def discard_scan(job_id: str) -> None:
    _validate_job_id(job_id)
    cleanup_scan(job_id)
# ---------------------------------------------------------------------------
# Feature 2: pull-request review
# ---------------------------------------------------------------------------


def _slug(value: str) -> str:
    """Accept `owner/repo` or a GitHub URL and return `owner/repo`."""
    candidate = (value or "").strip()
    match = re.search(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", candidate)
    if match:
        return f"{match.group(1)}/{match.group(2).removesuffix('.git')}"
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", candidate):
        return candidate
    raise HTTPException(status_code=422, detail="Expected a repository in the form owner/repo.")


@router.post("/review", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED)
async def start_review(request: ReviewRequest) -> JobAccepted:
    """Review an existing pull request (manual trigger for Feature 2)."""
    from .review import ReviewReport, review_pull_request
    from .review_github import ReviewError, fetch_pull_request, post_review

    slug = _slug(request.repo)
    job = manager.create("review", repo=slug, pull_number=request.pull_number)

    async def worker(current: Job) -> ReviewReport:
        current.log(f"Fetching {slug}#{request.pull_number}", "fetch", "info")
        info = await fetch_pull_request(slug, request.pull_number)
        current.log(f"Reviewing {len(info.patch.splitlines())} diff line(s)", "review", "info")

        report = await review_pull_request(
            repo=slug,
            patch=info.patch,
            pull_number=request.pull_number,
            commit_sha=info.head_sha,
            use_llm=request.use_llm,
        )
        current.log(report.summary, "review", "info")

        try:
            report = await post_review(report, commit_sha=info.head_sha)
        except ReviewError as exc:
            report.notes.append(f"Could not post the review: {exc}")
            current.log(str(exc), "post", "warn")

        if report.posted:
            current.log("Review posted with inline comments", "post", "success")
        else:
            current.log("Dry run: the review was not posted", "post", "info")

        current.progress = 100
        return report

    manager.start(job, worker)
    return JobAccepted(job_id=job.id)


@router.get("/review/{job_id}", response_model=ReviewJobResponse)
async def review_status(job_id: str) -> ReviewJobResponse:
    try:
        job = manager.require(_validate_job_id(job_id), "review")
    except KeyError:
        raise HTTPException(status_code=404, detail="Review job not found.") from None

    report = job.result.to_dict() if hasattr(job.result, "to_dict") else None
    return ReviewJobResponse(
        job_id=job.id,
        state=job.state,
        progress=job.progress,
        error=job.error,
        report=report,
        logs=job.logs,
    )


@router.post("/webhook/github", response_model=WebhookDelivery)
async def github_webhook(request: Request) -> WebhookDelivery:
    """Receive GitHub pull-request events and enqueue a review."""
    from .review import ReviewReport, review_pull_request
    from .review_github import ReviewError, fetch_pull_request, post_review
    from .webhook import WebhookError, delivery_key, parse_event, verify_signature

    settings = get_settings()
    body = await request.body()
    signature = request.headers.get("x-hub-signature-256")

    if settings.github_webhook_secret:
        if not verify_signature(body=body, signature_header=signature, secret=settings.github_webhook_secret):
            raise HTTPException(status_code=401, detail="Invalid webhook signature.")
    elif signature:
        raise HTTPException(
            status_code=401,
            detail="A signature was sent but no GITHUB_WEBHOOK_SECRET is configured.",
        )

    try:
        event = parse_event(event_header=request.headers.get("x-github-event"), body=body)
    except WebhookError as exc:
        return WebhookDelivery(accepted=False, message=str(exc))

    if event.draft:
        return WebhookDelivery(accepted=False, message="Draft pull requests are not reviewed yet.")

    key = delivery_key(event)
    if key in manager.store:
        return WebhookDelivery(accepted=False, message="This commit was already reviewed.")

    manager.store[key] = True
    job = manager.create("review", repo=event.slug, pull_number=event.number, delivery=key)

    async def worker(current: Job) -> ReviewReport:
        current.log(f"Webhook: {event.action} on {event.slug}#{event.number}", "webhook", "info")
        info = await fetch_pull_request(event.slug, event.number)
        report = await review_pull_request(
            repo=event.slug,
            patch=info.patch,
            pull_number=event.number,
            commit_sha=info.head_sha,
        )
        current.log(report.summary, "review", "info")
        try:
            report = await post_review(report, commit_sha=info.head_sha)
        except ReviewError as exc:
            report.notes.append(f"Could not post the review: {exc}")
        current.progress = 100
        return report

    manager.start(job, worker)
    return WebhookDelivery(accepted=True, message="Review queued.", job_id=job.id)


@router.get("/learned/{repo_path:path}")
async def learned_patterns(repo_path: str) -> dict:
    """Repository-specific issues the learner has recorded."""
    from .learner import Learner

    repo = _slug(repo_path)
    patterns = Learner().patterns_for(repo)
    return {"repo": repo, "patterns": [pattern.to_dict() for pattern in patterns]}
