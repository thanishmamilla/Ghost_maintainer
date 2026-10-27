"""Rot Score tests: component boundaries, missing data and label thresholds."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models import MetadataStatus, PackageHealth, Severity, Vulnerability
from app.rot_score import (
    MAX_ABANDONMENT,
    MAX_INACTIVITY,
    MAX_STALENESS,
    MAX_VERSION_LAG,
    MAX_VULNERABILITIES,
    compute_rot_score,
    days_since,
    label_for,
)

REFERENCE = datetime(2026, 10, 26, tzinfo=timezone.utc)


def days_ago(days: int) -> datetime:
    return REFERENCE - timedelta(days=days)


def health(
    *,
    published_at: datetime | None = None,
    latest_published_at: datetime | None = None,
    deprecated: bool | None = None,
    archived: bool | None = None,
    pushed_at: datetime | None = None,
) -> PackageHealth:
    return PackageHealth(
        published_at=published_at,
        latest_published_at=latest_published_at,
        deprecated=deprecated,
        repo_archived=archived,
        repo_pushed_at=pushed_at,
    )


def score(**kwargs):
    vulnerabilities = kwargs.pop("vulnerabilities", [])
    installed = kwargs.pop("installed", "1.0.0")
    latest = kwargs.pop("latest", "1.0.0")
    return compute_rot_score(
        health=health(**kwargs),
        vulnerabilities=vulnerabilities,
        installed_version=installed,
        latest_version=latest,
        reference_time=REFERENCE,
    )


def component(result, key):
    return next(item for item in result.components if item.key == key)


# ---------------------------------------------------------------------------
# staleness boundaries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("age_days", "expected"),
    [
        (1096, 30),
        (1095, 20),
        (731, 20),
        (730, 10),
        (366, 10),
        (365, 0),
        (0, 0),
    ],
)
def test_staleness_boundaries(age_days: int, expected: int) -> None:
    result = score(published_at=days_ago(age_days))

    assert component(result, "staleness").points == expected


def test_staleness_falls_back_to_the_latest_publish_date() -> None:
    result = score(published_at=None, latest_published_at=days_ago(800))

    assert component(result, "staleness").points == 20


def test_unknown_publish_date_is_not_treated_as_stale() -> None:
    result = score(published_at=None)

    staleness = component(result, "staleness")
    assert staleness.points == 0
    assert staleness.known is False
    assert "unknown" in staleness.reason.lower()


# ---------------------------------------------------------------------------
# abandonment
# ---------------------------------------------------------------------------


def test_deprecated_package_scores_the_deprecation_weight() -> None:
    result = score(deprecated=True)

    assert component(result, "abandonment").points == 25


def test_archived_repository_scores_the_archive_weight() -> None:
    result = score(archived=True)

    assert component(result, "abandonment").points == 15


def test_abandonment_uses_the_maximum_not_the_sum() -> None:
    result = score(deprecated=True, archived=True)

    assert component(result, "abandonment").points == MAX_ABANDONMENT


def test_unknown_abandonment_data_is_not_treated_as_abandoned() -> None:
    result = score()

    abandonment = component(result, "abandonment")
    assert abandonment.points == 0
    assert abandonment.known is False


def test_confirmed_maintained_package_scores_zero() -> None:
    result = score(deprecated=False, archived=False)

    assert component(result, "abandonment").points == 0
    assert component(result, "abandonment").known is True


# ---------------------------------------------------------------------------
# vulnerabilities
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("severity", "expected"),
    [
        (Severity.CRITICAL, 25),
        (Severity.HIGH, 18),
        (Severity.MEDIUM, 10),
        (Severity.LOW, 5),
    ],
)
def test_vulnerability_weight_by_severity(severity: Severity, expected: int) -> None:
    result = score(vulnerabilities=[Vulnerability(id="GHSA-test", severity=severity)])

    assert component(result, "vulnerabilities").points == expected


def test_highest_severity_wins() -> None:
    result = score(
        vulnerabilities=[
            Vulnerability(id="a", severity=Severity.LOW),
            Vulnerability(id="b", severity=Severity.CRITICAL),
            Vulnerability(id="c", severity=Severity.MEDIUM),
        ]
    )

    assert component(result, "vulnerabilities").points == MAX_VULNERABILITIES


def test_advisories_without_a_severity_do_not_invent_one() -> None:
    result = score(vulnerabilities=[Vulnerability(id="a", severity=Severity.UNKNOWN)])

    component_entry = component(result, "vulnerabilities")
    assert component_entry.points == 0
    assert component_entry.known is False


def test_no_advisories_scores_zero() -> None:
    result = score()

    assert component(result, "vulnerabilities").points == 0
    assert component(result, "vulnerabilities").known is True


# ---------------------------------------------------------------------------
# maintainer inactivity
# ---------------------------------------------------------------------------


def test_repository_pushed_over_a_year_ago_scores_inactivity() -> None:
    result = score(pushed_at=days_ago(400))

    assert component(result, "inactivity").points == MAX_INACTIVITY


def test_recently_pushed_repository_scores_no_inactivity() -> None:
    result = score(pushed_at=days_ago(30))

    assert component(result, "inactivity").points == 0


def test_unknown_push_date_is_not_treated_as_inactivity() -> None:
    result = score(pushed_at=None)

    inactivity = component(result, "inactivity")
    assert inactivity.points == 0
    assert inactivity.known is False


def test_a_release_date_is_never_used_as_repository_activity() -> None:
    """A recently published package with a dead repository is still inactive."""
    result = score(published_at=days_ago(5), pushed_at=days_ago(900))

    assert component(result, "inactivity").points == MAX_INACTIVITY
    assert component(result, "staleness").points == 0


# ---------------------------------------------------------------------------
# version lag
# ---------------------------------------------------------------------------


def test_major_version_gap_scores_version_lag() -> None:
    result = score(installed="1.2.0", latest="2.0.0")

    assert component(result, "version_lag").points == MAX_VERSION_LAG


def test_minor_version_gap_scores_no_version_lag() -> None:
    result = score(installed="1.2.0", latest="1.9.0")

    assert component(result, "version_lag").points == 0


def test_unknown_installed_version_cannot_score_version_lag() -> None:
    result = score(installed=None, latest="2.0.0")

    lag = component(result, "version_lag")
    assert lag.points == 0
    assert lag.known is False


# ---------------------------------------------------------------------------
# totals and labels
# ---------------------------------------------------------------------------


def test_score_is_the_sum_of_components() -> None:
    result = score(
        published_at=days_ago(2000),
        deprecated=True,
        archived=True,
        vulnerabilities=[Vulnerability(id="a", severity=Severity.CRITICAL)],
        pushed_at=days_ago(900),
        installed="1.0.0",
        latest="2.0.0",
    )

    expected = 30 + 25 + 25 + 10 + 10
    assert result.value == expected
    assert sum(item.points for item in result.components) == expected


def test_score_never_exceeds_one_hundred() -> None:
    result = score(
        published_at=days_ago(5000),
        deprecated=True,
        archived=True,
        vulnerabilities=[Vulnerability(id="a", severity=Severity.CRITICAL)],
        pushed_at=days_ago(5000),
        installed="1.0.0",
        latest="9.0.0",
    )

    assert result.value == 100


def test_perfectly_healthy_package_scores_zero() -> None:
    result = score(
        published_at=days_ago(10),
        deprecated=False,
        archived=False,
        pushed_at=days_ago(10),
        installed="1.0.0",
        latest="1.0.0",
    )

    assert result.value == 0
    assert result.label.value == "healthy"


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, "healthy"), (29, "healthy"), (30, "aging"), (59, "aging"), (60, "rotten"), (100, "rotten")],
)
def test_label_thresholds(value: int, expected: str) -> None:
    assert label_for(value).value == expected


def test_every_component_is_reported_with_a_reason() -> None:
    result = score(published_at=days_ago(500), pushed_at=days_ago(500))

    assert len(result.components) == 5
    assert all(component.reason for component in result.components)
    assert result.reasons


def test_metadata_status_is_partial_when_some_data_is_missing() -> None:
    result = score(published_at=days_ago(10), pushed_at=None)

    assert result.metadata_status is MetadataStatus.PARTIAL


def test_metadata_status_is_unavailable_when_nothing_is_known() -> None:
    result = compute_rot_score(
        health=health(),
        vulnerabilities=[],
        installed_version=None,
        latest_version=None,
        reference_time=REFERENCE,
        advisory_data_available=False,
    )

    assert result.metadata_status is MetadataStatus.UNAVAILABLE


def test_an_unreachable_advisory_service_is_not_reported_as_clean() -> None:
    """An outage must not look like "no known advisories"."""
    result = compute_rot_score(
        health=health(published_at=days_ago(10), pushed_at=days_ago(10)),
        vulnerabilities=[],
        installed_version="1.0.0",
        latest_version="1.0.0",
        reference_time=REFERENCE,
        advisory_data_available=False,
    )

    vulnerabilities = component(result, "vulnerabilities")
    assert vulnerabilities.points == 0
    assert vulnerabilities.known is False
    assert "could not be retrieved" in vulnerabilities.reason


def test_days_since_handles_naive_and_aware_datetimes() -> None:
    aware = days_ago(10)
    naive = aware.replace(tzinfo=None)

    assert days_since(aware, REFERENCE) == 10
    assert days_since(naive, REFERENCE) == 10
    assert days_since(None, REFERENCE) is None
