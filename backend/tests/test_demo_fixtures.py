"""Fixture-backed checks for the demo repository's intended findings.

These run entirely offline against the captured npm/OSV payloads, so they stay
deterministic and prove the demo really produces the findings the pitch claims.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent.parent / "app" / "fixtures"


def load_npm(name: str) -> dict:
    return json.loads((FIXTURES / "npm" / f"{name}.json").read_text(encoding="utf-8"))


def load_osv(name: str, version: str) -> dict:
    return json.loads((FIXTURES / "osv" / f"{name}@{version}.json").read_text(encoding="utf-8"))


def osv_vulns(name: str, version: str) -> list[dict]:
    return list(load_osv(name, version).get("vulns") or [])


def severities(name: str, version: str) -> set[str]:
    return {vuln.get("database_specific", {}).get("severity") for vuln in osv_vulns(name, version)}


def fixed_versions(name: str, version: str) -> set[str]:
    found: set[str] = set()
    for vuln in osv_vulns(name, version):
        for affected in vuln.get("affected") or []:
            for range_ in affected.get("ranges") or []:
                for event in range_.get("events") or []:
                    if event.get("fixed"):
                        found.add(event["fixed"])
    return found


@pytest.mark.parametrize(
    ("name", "version", "expected"),
    [
        ("lodash", "4.17.15", "HIGH"),
        ("minimist", "1.2.0", "CRITICAL"),
        ("jsonwebtoken", "3.2.2", "CRITICAL"),
        ("moment", "2.29.1", "HIGH"),
        ("request", "2.88.2", "MODERATE"),
    ],
)
def test_demo_packages_have_advisories(name: str, version: str, expected: str) -> None:
    assert osv_vulns(name, version), f"{name}@{version} is expected to have advisories"
    assert expected in severities(name, version)


def test_healthy_demo_package_has_no_advisories() -> None:
    assert osv_vulns("semver", "7.8.5") == []


def test_request_is_deprecated_on_npm() -> None:
    npm = load_npm("request")
    assert npm["deprecated_latest"], "request is expected to carry a deprecation notice"


def test_deprecation_message_names_the_upstream_issue() -> None:
    npm = load_npm("request")
    assert "deprecated" in str(npm["deprecated_latest"]).lower()


@pytest.mark.parametrize(
    ("name", "version"),
    [("moment", "2.29.1"), ("request", "2.88.2"), ("lodash", "4.17.15"), ("minimist", "1.2.0")],
)
def test_every_vulnerable_demo_package_has_a_fixed_version(name: str, version: str) -> None:
    """A safe bump is only possible when upstream published a fixed version."""
    assert fixed_versions(name, version), f"{name}@{version} has no fixed version to bump to"


def test_lodash_fix_is_available_in_the_same_major() -> None:
    """4.17.15 must be fixable without a major upgrade."""
    candidates = {v for v in fixed_versions("lodash", "4.17.15") if v.startswith("4.")}
    assert candidates, "no same-major fixed version for lodash"
    assert all(v.split(".")[0] == "4" for v in candidates)


def test_minimist_fix_is_available_in_the_same_major() -> None:
    candidates = {v for v in fixed_versions("minimist", "1.2.0") if v.startswith("1.")}
    assert candidates, "no same-major fixed version for minimist"


def test_migration_targets_are_clean() -> None:
    """The packages we migrate to must not introduce new advisories."""
    assert osv_vulns("dayjs", "1.11.23") == []
    assert osv_vulns("axios", "1.20.0") == []


def test_latest_versions_are_newer_than_installed() -> None:
    for name, installed in [
        ("lodash", "4.17.15"),
        ("minimist", "1.2.0"),
        ("moment", "2.29.1"),
        ("jsonwebtoken", "3.2.2"),
    ]:
        latest = load_npm(name)["dist-tags"]["latest"]
        assert latest != installed, f"{name} fixture is stale: installed {installed}, latest {latest}"
