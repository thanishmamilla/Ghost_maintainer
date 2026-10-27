"""API tests: validation, job lifecycle and error handling."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import manager
from app.config import get_settings
from app.main import app
from app.models import (
    DependencyReport,
    JobState,
    MetadataStatus,
    PackageHealth,
    ReachabilityClass,
    ReachabilityResult,
    RiskLabel,
    RotScore,
    ScanResult,
    ScanSummary,
)


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# input validation
# ---------------------------------------------------------------------------


def test_scan_rejects_an_empty_repository(client: TestClient) -> None:
    response = client.post("/api/scan", json={"repo_url": ""})

    assert response.status_code == 422


def test_scan_rejects_an_overlong_repository(client: TestClient) -> None:
    response = client.post("/api/scan", json={"repo_url": "x" * 600})

    assert response.status_code == 422


def test_unknown_scan_job_returns_404(client: TestClient) -> None:
    response = client.get("/api/scan/deadbeef")

    assert response.status_code == 404


def test_malformed_job_id_is_rejected(client: TestClient) -> None:
    response = client.get("/api/scan/..%2F..%2Fetc%2Fpasswd")

    assert response.status_code == 404


def test_fix_requires_an_existing_scan(client: TestClient) -> None:
    response = client.post("/api/fix", json={"scan_id": "nosuchjob", "package": "lodash"})

    assert response.status_code == 404


@pytest.mark.parametrize(
    "package",
    ["../../etc/passwd", "lodash; rm -rf /", "lodash/../../x", "", "@", "UPPER"],
)
def test_fix_rejects_dangerous_package_names(client: TestClient, package: str) -> None:
    job = manager.create("scan", repo_url="demo")
    job.state = JobState.SUCCEEDED
    job.result = ScanResult(
        scan_id=job.id,
        repo_url="demo",
        repo_name="demo-rotten-app",
        dependencies=[],
        summary=ScanSummary(),
    )

    response = client.post("/api/fix", json={"scan_id": job.id, "package": package})

    assert response.status_code in {404, 422}


def test_fix_rejects_a_package_outside_the_scan(client: TestClient, tmp_path: Path) -> None:
    job = manager.create("scan", repo_url="demo")
    job.state = JobState.SUCCEEDED
    job.result = ScanResult(
        scan_id=job.id,
        repo_url="demo",
        repo_name="demo-rotten-app",
        dependencies=[dependency_report("lodash")],
        summary=ScanSummary(total_dependencies=1),
    )
    manager.store[f"scan:{job.id}"] = type("Scanned", (), {"root": str(tmp_path)})()

    response = client.post("/api/fix", json={"scan_id": job.id, "package": "express"})

    assert response.status_code == 404
    assert "not part of this scan" in response.json()["detail"]


def test_fix_rejects_a_scan_that_has_not_finished(client: TestClient) -> None:
    job = manager.create("scan", repo_url="demo")
    job.state = JobState.RUNNING

    response = client.post("/api/fix", json={"scan_id": job.id, "package": "lodash"})

    assert response.status_code == 409


def test_fix_reports_a_missing_working_copy(client: TestClient, tmp_path: Path) -> None:
    job = manager.create("scan", repo_url="demo")
    job.state = JobState.SUCCEEDED
    job.result = ScanResult(
        scan_id=job.id,
        repo_url="demo",
        repo_name="demo-rotten-app",
        dependencies=[dependency_report("lodash")],
        summary=ScanSummary(total_dependencies=1),
    )

    response = client.post("/api/fix", json={"scan_id": job.id, "package": "lodash"})

    assert response.status_code == 410


# ---------------------------------------------------------------------------
# job lifecycle
# ---------------------------------------------------------------------------


def test_scan_job_reports_its_state(client: TestClient) -> None:
    job = manager.create("scan", repo_url="demo")
    job.state = JobState.SUCCEEDED
    job.progress = 100
    job.result = ScanResult(
        scan_id=job.id,
        repo_url="demo",
        repo_name="demo-rotten-app",
        dependencies=[dependency_report("lodash")],
        summary=ScanSummary(total_dependencies=1, aging=1, total_alerts=1),
    )

    response = client.get(f"/api/scan/{job.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "succeeded"
    assert payload["result"]["summary"]["total_dependencies"] == 1
    assert payload["result"]["dependencies"][0]["name"] == "lodash"


def test_failed_job_surfaces_its_error(client: TestClient) -> None:
    job = manager.create("scan", repo_url="demo")
    job.state = JobState.FAILED
    job.error = "Could not clone the repository."

    response = client.get(f"/api/scan/{job.id}")

    assert response.status_code == 200
    assert response.json()["error"] == "Could not clone the repository."


def test_a_job_that_is_still_running_has_no_result(client: TestClient) -> None:
    job = manager.create("scan", repo_url="demo")
    job.state = JobState.RUNNING

    payload = client.get(f"/api/scan/{job.id}").json()

    assert payload["state"] == "running"
    assert payload["result"] is None


def test_a_scan_of_an_invalid_repository_fails_the_job(client: TestClient) -> None:
    response = client.post("/api/scan", json={"repo_url": "not-a-real-repository"})
    job_id = response.json()["job_id"]

    for _ in range(60):
        payload = client.get(f"/api/scan/{job_id}").json()
        if payload["state"] in {"succeeded", "failed"}:
            break
        asyncio.run(asyncio.sleep(0.1))

    assert payload["state"] == "failed"
    assert payload["error"]


def test_scan_status_exposes_the_log(client: TestClient) -> None:
    job = manager.create("scan", repo_url="demo")
    job.state = JobState.RUNNING
    job.log("Resolving repository", "resolve", "info")

    payload = client.get(f"/api/scan/{job.id}").json()

    assert payload["logs"]
    assert payload["logs"][0]["message"] == "Resolving repository"


# ---------------------------------------------------------------------------
# streaming
# ---------------------------------------------------------------------------


def test_stream_rejects_an_unknown_job(client: TestClient) -> None:
    response = client.get("/api/fix/unknownstream/stream")

    assert response.status_code == 404


def test_stream_rejects_a_scan_job(client: TestClient) -> None:
    job = manager.create("scan", repo_url="demo")

    response = client.get(f"/api/fix/{job.id}/stream")

    assert response.status_code == 404


def test_stream_emits_events_for_a_finished_job(client: TestClient) -> None:
    job = manager.create("fix", scan_id="x", package="lodash")
    job.log("Preparing workspace", "sandbox", "info")
    job.state = JobState.SUCCEEDED
    job.finished.set()

    with client.stream("GET", f"/api/fix/{job.id}/stream") as response:
        body = "".join(response.iter_text())

    assert "event: log" in body
    assert "Preparing workspace" in body
    assert "event: done" in body


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def dependency_report(name: str) -> DependencyReport:
    return DependencyReport(
        name=name,
        version="4.17.15",
        version_source="lockfile",
        declared_range="4.17.15",
        health=PackageHealth(metadata_status=MetadataStatus.OK),
        rot=RotScore(value=48, label=RiskLabel.AGING, metadata_status=MetadataStatus.OK),
        reachability=ReachabilityResult(
            classification=ReachabilityClass.OBSERVED, explanation="observed"
        ),
    )
# ---------------------------------------------------------------------------
# review endpoints
# ---------------------------------------------------------------------------


def test_review_rejects_an_invalid_repository(client: TestClient) -> None:
    response = client.post("/api/review", json={"repo": "not a repo", "pull_number": 1})

    assert response.status_code == 422


def test_review_rejects_an_invalid_pull_number(client: TestClient) -> None:
    response = client.post("/api/review", json={"repo": "owner/repo", "pull_number": 0})

    assert response.status_code == 422


def test_unknown_review_job_returns_404(client: TestClient) -> None:
    assert client.get("/api/review/deadbeef").status_code == 404


def test_webhook_ignores_unrelated_events(client: TestClient) -> None:
    response = client.post(
        "/api/webhook/github",
        json={"action": "opened"},
        headers={"x-github-event": "push", "content-type": "application/json"},
    )

    assert response.status_code == 200
    assert response.json()["accepted"] is False


def test_webhook_rejects_a_signature_without_a_configured_secret(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "github_webhook_secret", "")

    response = client.post(
        "/api/webhook/github",
        json={"action": "opened"},
        headers={
            "x-github-event": "pull_request",
            "x-hub-signature-256": "sha256=abc",
            "content-type": "application/json",
        },
    )

    assert response.status_code == 401


def test_webhook_rejects_a_bad_signature(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "github_webhook_secret", "secret")

    response = client.post(
        "/api/webhook/github",
        content=b'{"action":"opened"}',
        headers={
            "x-github-event": "pull_request",
            "x-hub-signature-256": "sha256=deadbeef",
            "content-type": "application/json",
        },
    )

    assert response.status_code == 401
