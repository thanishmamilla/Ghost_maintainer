"""Typed contracts shared by the scanner, health engine, fixer, sandbox, PR bot and API."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RiskLabel(str, Enum):
    HEALTHY = "healthy"
    AGING = "aging"
    ROTTEN = "rotten"


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class ReachabilityClass(str, Enum):
    OBSERVED = "observed"
    NOT_OBSERVED = "not_observed"
    UNKNOWN = "unknown"


class MetadataStatus(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class FixType(str, Enum):
    VERSION_BUMP = "version_bump"
    PACKAGE_REPLACEMENT = "package_replacement"
    NONE = "none"


class SandboxStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    NEEDS_HUMAN_REVIEW = "needs_human_review"
    BASELINE_FAILED = "baseline_failed"
    TIMEOUT = "timeout"
    ERROR = "error"


VersionSource = Literal["lockfile", "unresolved"]
UsageKind = Literal["import", "require", "dynamic_import", "side_effect"]


class Dependency(BaseModel):
    """A direct npm dependency declared by the repository."""

    name: str
    version: str | None = None
    version_source: VersionSource = "unresolved"
    declared_range: str
    is_dev: bool = False
    latest_version: str | None = None
    repository_url: str | None = None
    license: str | None = None


class UsageMatch(BaseModel):
    file: str
    line: int
    column: int = 1
    snippet: str
    kind: UsageKind = "import"
    specifier: str = ""
    members: list[str] = Field(default_factory=list)


class ReachabilityResult(BaseModel):
    classification: ReachabilityClass
    explanation: str
    matches: list[UsageMatch] = Field(default_factory=list)
    used_members: list[str] = Field(default_factory=list)
    scanned_files: int = 0
    limitation: str = (
        "Source scanning is heuristic. A package with no observed import may still be "
        "reached transitively, dynamically, through path aliases or from unsupported files."
    )


class Vulnerability(BaseModel):
    id: str
    aliases: list[str] = Field(default_factory=list)
    summary: str = ""
    severity: Severity = Severity.UNKNOWN
    severity_source: Literal["database_specific", "cvss_vector", "unknown"] = "unknown"
    cvss_score: float | None = None
    affected_ranges: list[str] = Field(default_factory=list)
    fixed_versions: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    affected_functions: list[str] = Field(default_factory=list)


class PackageHealth(BaseModel):
    """Registry / repository metadata. Missing values stay None rather than being guessed."""

    latest_version: str | None = None
    published_at: datetime | None = None
    latest_published_at: datetime | None = None
    deprecated: bool | None = None
    deprecated_message: str | None = None
    repository_url: str | None = None
    license: str | None = None
    repo_archived: bool | None = None
    repo_pushed_at: datetime | None = None
    downloads_last_week: int | None = None
    scorecard_score: float | None = None
    metadata_status: MetadataStatus = MetadataStatus.UNAVAILABLE
    data_gaps: list[str] = Field(default_factory=list)


class ScoreComponent(BaseModel):
    key: Literal["staleness", "abandonment", "vulnerabilities", "inactivity", "version_lag"]
    label: str
    points: int
    max_points: int
    reason: str
    known: bool = True


class RotScore(BaseModel):
    value: int = Field(ge=0, le=100)
    label: RiskLabel
    components: list[ScoreComponent] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    computed_at: datetime = Field(default_factory=utcnow)
    metadata_status: MetadataStatus = MetadataStatus.UNAVAILABLE


class FixOption(BaseModel):
    type: FixType
    summary: str
    from_version: str | None = None
    to_version: str | None = None
    replacement: str | None = None
    requires_human_review: bool = False
    migration_notes: list[str] = Field(default_factory=list)


class DependencyReport(BaseModel):
    name: str
    version: str | None = None
    version_source: VersionSource = "unresolved"
    declared_range: str
    is_dev: bool = False
    latest_version: str | None = None
    repository_url: str | None = None
    license: str | None = None
    health: PackageHealth = Field(default_factory=PackageHealth)
    rot: RotScore
    vulnerabilities: list[Vulnerability] = Field(default_factory=list)
    reachability: ReachabilityResult
    fix_options: list[FixOption] = Field(default_factory=list)


class ScanSummary(BaseModel):
    total_dependencies: int = 0
    rotten: int = 0
    aging: int = 0
    healthy: int = 0
    total_alerts: int = 0
    alerts_with_observed_usage: int = 0
    resolved_versions: int = 0
    unresolved_versions: int = 0


class GraphNode(BaseModel):
    id: str
    label: str
    kind: Literal["project", "dependency"] = "dependency"
    rot: int = 0
    label_kind: RiskLabel = RiskLabel.HEALTHY
    is_dev: bool = False
    alerts: int = 0
    observed: bool = False


class DepGraph(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[dict[str, str]] = Field(default_factory=list)


class ScanResult(BaseModel):
    scan_id: str
    repo_url: str
    repo_name: str
    repo_slug: str | None = None
    ref: str | None = None
    base_commit: str | None = None
    scanned_at: datetime = Field(default_factory=utcnow)
    dependencies: list[DependencyReport] = Field(default_factory=list)
    summary: ScanSummary = Field(default_factory=ScanSummary)
    graph: DepGraph = Field(default_factory=DepGraph)
    errors: list[str] = Field(default_factory=list)


class FileChange(BaseModel):
    path: str
    diff: str
    content: str = ""
    added_lines: int = 0
    removed_lines: int = 0


class FixPlan(BaseModel):
    type: FixType
    package: str
    from_version: str | None = None
    to_version: str | None = None
    replacement: str | None = None
    files_to_change: list[str] = Field(default_factory=list)
    explanation: str = ""
    migration_notes: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    requires_human_review: bool = False
    changes: list[FileChange] = Field(default_factory=list)


class SandboxResult(BaseModel):
    status: SandboxStatus
    mode: Literal["docker", "local_restricted"] = "local_restricted"
    baseline_passed: bool | None = None
    after_passed: bool | None = None
    baseline_exit_code: int | None = None
    after_exit_code: int | None = None
    baseline_output_tail: str = ""
    test_output_tail: str = ""
    duration_seconds: float = 0.0
    diff: str = ""
    files_changed: list[str] = Field(default_factory=list)
    workspace_path: str | None = None
    notes: list[str] = Field(default_factory=list)
    error: str | None = None
    command: str = "npm test"


class PRPreview(BaseModel):
    mode: Literal["dry_run", "real"] = "dry_run"
    created: bool = False
    title: str
    body: str
    branch: str
    base_branch: str = "main"
    changed_files: list[str] = Field(default_factory=list)
    diff: str = ""
    url: str | None = None
    notes: list[str] = Field(default_factory=list)


class LogEntry(BaseModel):
    ts: datetime = Field(default_factory=utcnow)
    level: Literal["info", "warn", "error", "success"] = "info"
    step: str = ""
    message: str


class FixResult(BaseModel):
    fix_id: str
    scan_id: str
    package: str
    state: JobState = JobState.QUEUED
    plan: FixPlan | None = None
    sandbox: SandboxResult | None = None
    pr: PRPreview | None = None
    logs: list[LogEntry] = Field(default_factory=list)
    error: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"] = "ok"
    version: str
    dry_run: bool = True
    github_configured: bool = False
    llm_configured: bool = False
    sandbox_mode: str = "auto"
    time: datetime = Field(default_factory=utcnow)


class ScanRequest(BaseModel):
    repo_url: str = Field(min_length=1, max_length=500)
    include_dev: bool = True


class FixRequest(BaseModel):
    scan_id: str = Field(min_length=1, max_length=64)
    package: str = Field(min_length=1, max_length=214)
    fix_type: FixType | None = None
    apply_llm: bool = False


class JobAccepted(BaseModel):
    job_id: str


class JobStatusResponse(BaseModel):
    job_id: str
    state: JobState
    progress: int = 0
    error: str | None = None
    payload: dict[str, Any] | None = None


class ScanJobResponse(BaseModel):
    job_id: str
    state: JobState
    progress: int = 0
    error: str | None = None
    result: ScanResult | None = None
    logs: list[LogEntry] = Field(default_factory=list)


class FixJobResponse(BaseModel):
    job_id: str
    state: JobState
    progress: int = 0
    error: str | None = None
    result: FixResult | None = None
    logs: list[LogEntry] = Field(default_factory=list)


class ReviewRequest(BaseModel):
    repo: str = Field(min_length=1, max_length=200)
    pull_number: int = Field(ge=1, le=100_000_000)
    use_llm: bool = True


class ReviewJobResponse(BaseModel):
    job_id: str
    state: JobState
    progress: int = 0
    error: str | None = None
    report: dict[str, Any] | None = None
    logs: list[LogEntry] = Field(default_factory=list)


class WebhookDelivery(BaseModel):
    accepted: bool
    message: str
    job_id: str | None = None