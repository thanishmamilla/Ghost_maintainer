"""Rot Score computation.

The score is a transparent sum of five weighted components. Every component
reports its own points, its maximum and a plain-English reason, so the UI can
explain any score without re-implementing the arithmetic.

Interpretation notes, made explicit because the underlying spec is ambiguous:

* **Staleness** measures the age of the release that is actually installed
  (falling back to the latest release when the installed one is unknown).
  A dependency pinned to a seven-year-old release is stale even when the
  project still ships new versions; "a newer major exists" is a separate
  signal, captured by *version lag*.
* **Maintainer inactivity** uses the repository's last push, never the latest
  release timestamp, so a package that publishes tarballs from a dead
  repository is not mistaken for an active one.
* **Unknown data is never evidence of neglect.** A component whose input could
  not be established scores zero and is flagged ``known=False`` so the UI can
  say the data was unavailable rather than implying a clean result.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from .models import (
    MetadataStatus,
    PackageHealth,
    RiskLabel,
    RotScore,
    ScoreComponent,
    Severity,
    Vulnerability,
)
from .semver_utils import is_at_least_major_behind

# Component maxima, from the product specification.
MAX_STALENESS = 30
MAX_ABANDONMENT = 25
MAX_VULNERABILITIES = 25
MAX_INACTIVITY = 10
MAX_VERSION_LAG = 10

STALENESS_THRESHOLDS = ((1095, 30), (730, 20), (365, 10))
INACTIVITY_THRESHOLD_DAYS = 365

SEVERITY_POINTS: dict[Severity, int] = {
    Severity.CRITICAL: 25,
    Severity.HIGH: 18,
    Severity.MEDIUM: 10,
    Severity.LOW: 5,
    Severity.UNKNOWN: 0,
}

_ARCHIVED_POINTS = 15
_DEPRECATED_POINTS = 25


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def days_since(value: datetime | None, reference: datetime | None = None) -> int | None:
    """Whole days between `value` and `reference` (default: now)."""
    moment = _utc(value)
    if moment is None:
        return None
    base = _utc(reference) or datetime.now(timezone.utc)
    return (base - moment).days


def highest_severity(vulnerabilities: Iterable[Vulnerability]) -> Severity:
    """The most severe confirmed severity in a set of advisories."""
    order = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW]
    present = {vuln.severity for vuln in vulnerabilities}
    for severity in order:
        if severity in present:
            return severity
    return Severity.UNKNOWN


def _staleness_component(published_at: datetime | None, reference: datetime) -> ScoreComponent:
    age = days_since(published_at, reference)
    if age is None:
        return ScoreComponent(
            key="staleness",
            label="Staleness",
            points=0,
            max_points=MAX_STALENESS,
            reason="The publish date of this release is unknown, so staleness could not be assessed.",
            known=False,
        )

    for threshold, points in STALENESS_THRESHOLDS:
        if age > threshold:
            return ScoreComponent(
                key="staleness",
                label="Staleness",
                points=points,
                max_points=MAX_STALENESS,
                reason=f"The installed release was published {age} days ago (more than {threshold} days).",
            )

    return ScoreComponent(
        key="staleness",
        label="Staleness",
        points=0,
        max_points=MAX_STALENESS,
        reason=f"The installed release was published {age} days ago.",
    )


def _abandonment_component(health: PackageHealth) -> ScoreComponent:
    archived = health.repo_archived
    deprecated = health.deprecated

    if archived is None and deprecated is None:
        return ScoreComponent(
            key="abandonment",
            label="Abandonment",
            points=0,
            max_points=MAX_ABANDONMENT,
            reason="Neither an archive status nor a deprecation notice could be confirmed.",
            known=False,
        )

    candidates: list[tuple[int, str]] = []
    if deprecated:
        candidates.append((_DEPRECATED_POINTS, "The package is marked deprecated on the npm registry."))
    if archived:
        candidates.append((_ARCHIVED_POINTS, "The source repository is archived."))

    if candidates:
        points, reason = max(candidates, key=lambda item: item[0])
        return ScoreComponent(
            key="abandonment",
            label="Abandonment",
            points=points,
            max_points=MAX_ABANDONMENT,
            reason=reason,
        )

    parts = []
    if deprecated is False:
        parts.append("the package is not deprecated")
    if archived is False:
        parts.append("the repository is not archived")
    return ScoreComponent(
        key="abandonment",
        label="Abandonment",
        points=0,
        max_points=MAX_ABANDONMENT,
        reason="The package is maintained: " + " and ".join(parts) + ".",
    )


def _vulnerability_component(
    vulnerabilities: list[Vulnerability],
    advisory_data_available: bool = True,
) -> ScoreComponent:
    if not advisory_data_available:
        return ScoreComponent(
            key="vulnerabilities",
            label="Vulnerabilities",
            points=0,
            max_points=MAX_VULNERABILITIES,
            reason="Advisory data could not be retrieved, so known vulnerabilities could not be assessed.",
            known=False,
        )

    if not vulnerabilities:
        return ScoreComponent(
            key="vulnerabilities",
            label="Vulnerabilities",
            points=0,
            max_points=MAX_VULNERABILITIES,
            reason="No known advisories affect the installed version.",
        )

    severity = highest_severity(vulnerabilities)
    if severity is Severity.UNKNOWN:
        return ScoreComponent(
            key="vulnerabilities",
            label="Vulnerabilities",
            points=0,
            max_points=MAX_VULNERABILITIES,
            reason=(
                f"{len(vulnerabilities)} advisory/advisories affect this version, but none carries a "
                "severity rating that can be used for scoring."
            ),
            known=False,
        )

    points = SEVERITY_POINTS[severity]
    return ScoreComponent(
        key="vulnerabilities",
        label="Vulnerabilities",
        points=points,
        max_points=MAX_VULNERABILITIES,
        reason=f"{len(vulnerabilities)} advisory/advisories affect this version; the highest is {severity.value}.",
    )


def _inactivity_component(pushed_at: datetime | None, reference: datetime) -> ScoreComponent:
    age = days_since(pushed_at, reference)
    if age is None:
        return ScoreComponent(
            key="inactivity",
            label="Maintainer inactivity",
            points=0,
            max_points=MAX_INACTIVITY,
            reason="The repository's last push could not be determined.",
            known=False,
        )

    if age > INACTIVITY_THRESHOLD_DAYS:
        return ScoreComponent(
            key="inactivity",
            label="Maintainer inactivity",
            points=MAX_INACTIVITY,
            max_points=MAX_INACTIVITY,
            reason=f"The repository has not been pushed to for {age} days.",
        )

    return ScoreComponent(
        key="inactivity",
        label="Maintainer inactivity",
        points=0,
        max_points=MAX_INACTIVITY,
        reason=f"The repository was last pushed to {age} days ago.",
    )


def _version_lag_component(installed: str | None, latest: str | None) -> ScoreComponent:
    if not installed or not latest:
        return ScoreComponent(
            key="version_lag",
            label="Version lag",
            points=0,
            max_points=MAX_VERSION_LAG,
            reason="The installed or the latest version is unknown, so the gap could not be measured.",
            known=False,
        )

    if is_at_least_major_behind(installed, latest):
        return ScoreComponent(
            key="version_lag",
            label="Version lag",
            points=MAX_VERSION_LAG,
            max_points=MAX_VERSION_LAG,
            reason=f"Version {installed} is at least one major version behind {latest}.",
        )

    return ScoreComponent(
        key="version_lag",
        label="Version lag",
        points=0,
        max_points=MAX_VERSION_LAG,
        reason=f"Version {installed} is not a major version behind {latest}.",
    )


def label_for(value: int) -> RiskLabel:
    """Map a score onto its label."""
    if value >= 60:
        return RiskLabel.ROTTEN
    if value >= 30:
        return RiskLabel.AGING
    return RiskLabel.HEALTHY


def compute_rot_score(
    *,
    health: PackageHealth,
    vulnerabilities: list[Vulnerability],
    installed_version: str | None,
    latest_version: str | None,
    reference_time: datetime | None = None,
    advisory_data_available: bool = True,
) -> RotScore:
    """Calculate the Rot Score and its full breakdown.

    ``advisory_data_available`` distinguishes "the advisory service reported no
    vulnerabilities" from "the advisory service could not be reached". Without
    it an outage would silently look like a clean bill of health.
    """
    reference = _utc(reference_time) or datetime.now(timezone.utc)

    # The staleness signal describes the release in use, so prefer the installed
    # version's publish date and fall back to the latest release's date.
    published = health.published_at or health.latest_published_at

    components = [
        _staleness_component(published, reference),
        _abandonment_component(health),
        _vulnerability_component(vulnerabilities, advisory_data_available),
        _inactivity_component(health.repo_pushed_at, reference),
        _version_lag_component(installed_version, latest_version),
    ]

    value = min(100, sum(component.points for component in components))
    reasons = [component.reason for component in components if component.points > 0]

    known_components = [component for component in components if component.known]
    if not known_components:
        status = MetadataStatus.UNAVAILABLE
    elif len(known_components) == len(components):
        status = MetadataStatus.OK
    else:
        status = MetadataStatus.PARTIAL

    return RotScore(
        value=value,
        label=label_for(value),
        components=components,
        reasons=reasons,
        computed_at=reference,
        metadata_status=status,
    )
