"""Fixer tests: safe bump selection, codemods and refusal of unsupported patterns."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.codemods import apply_codemod, moment_to_dayjs, request_to_axios
from app.fixer import analyse_bump, build_bump_plan, build_replacement_plan, offer_fix_options
from app.models import (
    DependencyReport,
    FixType,
    MetadataStatus,
    PackageHealth,
    ReachabilityClass,
    ReachabilityResult,
    RotScore,
    Severity,
    Vulnerability,
)

DEMO = Path(__file__).resolve().parent.parent.parent / "demo-rotten-app"


def report_for(
    name: str,
    version: str | None,
    vulnerabilities: list[Vulnerability],
    latest: str | None = None,
) -> DependencyReport:
    return DependencyReport(
        name=name,
        version=version,
        version_source="lockfile" if version else "unresolved",
        declared_range="^1.0.0",
        latest_version=latest,
        health=PackageHealth(metadata_status=MetadataStatus.OK),
        rot=RotScore(value=50, label="rotten", metadata_status=MetadataStatus.OK),
        vulnerabilities=vulnerabilities,
        reachability=ReachabilityResult(classification=ReachabilityClass.OBSERVED, explanation=""),
    )


def vuln(identifier: str, severity: Severity, fixed: list[str]) -> Vulnerability:
    return Vulnerability(id=identifier, severity=severity, fixed_versions=fixed)


# ---------------------------------------------------------------------------
# safe bump selection
# ---------------------------------------------------------------------------


def test_bump_targets_the_lowest_version_that_fixes_every_advisory() -> None:
    report = report_for(
        "lodash",
        "4.17.15",
        [
            vuln("A", Severity.HIGH, ["4.17.21"]),
            vuln("B", Severity.HIGH, ["4.17.19"]),
            vuln("C", Severity.MEDIUM, ["4.17.23"]),
        ],
    )

    analysis = analyse_bump(report)

    assert analysis.target == "4.17.23"


def test_bump_uses_the_highest_fixed_marker_across_advisories() -> None:
    report = report_for(
        "lodash",
        "4.17.15",
        [
            vuln("A", Severity.HIGH, ["4.17.21"]),
            vuln("B", Severity.HIGH, ["4.18.0"]),
        ],
    )

    assert analyse_bump(report).target == "4.18.0"


def test_cross_major_fixes_are_not_applied_automatically() -> None:
    report = report_for("jsonwebtoken", "3.2.2", [vuln("A", Severity.CRITICAL, ["9.0.0"])])

    analysis = analyse_bump(report)

    assert analysis.target is None
    assert analysis.cross_major_only is True
    assert any("breaking change" in note for note in analysis.notes)


def test_no_advisory_means_no_bump() -> None:
    report = report_for("semver", "7.8.5", [])

    analysis = analyse_bump(report)

    assert analysis.target is None
    assert analysis.notes


def test_unknown_installed_version_prevents_a_bump() -> None:
    report = report_for("ghost", None, [vuln("A", Severity.HIGH, ["1.0.0"])])

    assert analyse_bump(report).target is None


def test_a_fixed_version_older_than_installed_is_ignored() -> None:
    report = report_for("pkg", "2.0.0", [vuln("A", Severity.HIGH, ["1.5.0"])])

    analysis = analyse_bump(report)

    assert analysis.target is None


def test_bump_plan_updates_the_manifest() -> None:
    report = report_for("lodash", "4.17.15", [vuln("A", Severity.HIGH, ["4.17.21"])])

    plan = build_bump_plan(report, DEMO)

    assert plan.type is FixType.VERSION_BUMP
    assert plan.to_version == "4.17.21"
    assert "package.json" in plan.files_to_change
    assert plan.requires_human_review is False
    assert plan.changes
    assert "lodash" in plan.changes[0].diff


def test_bump_plan_records_evidence() -> None:
    report = report_for("lodash", "4.17.15", [vuln("GHSA-1", Severity.HIGH, ["4.17.21"])])

    plan = build_bump_plan(report, DEMO)

    assert any("GHSA-1" in item for item in plan.evidence)


def test_bump_plan_is_refused_without_a_safe_target() -> None:
    report = report_for("jsonwebtoken", "3.2.2", [vuln("A", Severity.CRITICAL, ["9.0.0"])])

    plan = build_bump_plan(report, DEMO)

    assert plan.requires_human_review is True
    assert plan.type is FixType.NONE


# ---------------------------------------------------------------------------
# moment -> dayjs codemod
# ---------------------------------------------------------------------------


def test_moment_import_and_usages_are_renamed() -> None:
    source = 'const moment = require("moment");\nconst x = moment("2024-01-01").format("YYYY-MM-DD");\n'

    result = moment_to_dayjs({"src/dates.js": source})

    assert result.supported
    updated = result.edits[0].updated
    assert 'require("dayjs")' in updated
    assert "dayjs(" in updated
    assert "moment" not in updated


def test_moment_esm_import_is_renamed() -> None:
    source = 'import moment from "moment";\nmoment();\n'

    result = moment_to_dayjs({"src/dates.js": source})

    assert result.supported
    assert 'import dayjs from "dayjs";' in result.edits[0].updated


def test_moment_add_and_subtract_are_supported() -> None:
    source = (
        'const moment = require("moment");\n'
        'moment(value).add(3, "days");\n'
        'moment(value).subtract(1, "months");\n'
    )

    result = moment_to_dayjs({"src/dates.js": source})

    assert result.supported
    updated = result.edits[0].updated
    assert 'dayjs(value).add(3, "days")' in updated
    assert 'dayjs(value).subtract(1, "months")' in updated


def test_unsupported_moment_api_is_refused() -> None:
    source = 'const moment = require("moment");\nmoment.locale("de");\n'

    result = moment_to_dayjs({"src/dates.js": source})

    assert result.supported is False
    assert "locale" in result.reason


def test_moment_calendar_is_refused() -> None:
    source = 'const moment = require("moment");\nmoment().calendar();\n'

    result = moment_to_dayjs({"src/dates.js": source})

    assert result.supported is False


def test_named_moment_import_is_refused() -> None:
    source = 'import { duration } from "moment";\n'

    result = moment_to_dayjs({"src/dates.js": source})

    assert result.supported is False
    assert "named members" in result.reason


def test_files_without_moment_are_untouched() -> None:
    result = moment_to_dayjs({"src/other.js": 'const x = require("other");\n'})

    assert result.supported is False
    assert result.edits == []


def test_moment_codemod_leaves_property_accesses_alone() -> None:
    source = 'const moment = require("moment");\nobj.moment = 1;\nmoment();\n'

    result = moment_to_dayjs({"src/dates.js": source})

    updated = result.edits[0].updated
    assert "obj.moment = 1;" in updated
    assert "dayjs();" in updated


# ---------------------------------------------------------------------------
# request -> axios codemod
# ---------------------------------------------------------------------------


def test_request_client_is_replaced_with_an_axios_adapter() -> None:
    source = (
        'const request = require("request");\n'
        "function fetchRaw(url, client = request) {\n"
        "  return client.get(url, (error, response, body) => resolve(body));\n"
        "}\n"
    )

    result = request_to_axios({"src/http.js": source})

    assert result.supported
    updated = result.edits[0].updated
    assert 'require("axios")' in updated
    assert "httpClient" in updated
    assert "client = httpClient" in updated
    assert "require(\"request\")" not in updated


def test_direct_request_usage_is_refused() -> None:
    source = (
        'const request = require("request");\n'
        'request.get(url, (error, response, body) => resolve(body));\n'
    )

    result = request_to_axios({"src/http.js": source})

    assert result.supported is False
    assert "directly" in result.reason


def test_request_post_is_refused() -> None:
    source = (
        'const request = require("request");\n'
        "function post(url, client = request) {\n"
        "  return client.post(url, body);\n"
        "}\n"
    )

    result = request_to_axios({"src/http.js": source})

    assert result.supported is False


def test_axios_adapter_preserves_non_2xx_behaviour() -> None:
    source = (
        'const request = require("request");\n'
        "function fetchRaw(url, client = request) {\n"
        "  return client.get(url, (error, response, body) => resolve(body));\n"
        "}\n"
    )

    result = request_to_axios({"src/http.js": source})
    updated = result.edits[0].updated

    assert "error.response" in updated
    assert "statusCode: error.response.status" in updated


# ---------------------------------------------------------------------------
# replacement plans
# ---------------------------------------------------------------------------


def test_moment_replacement_plan_against_the_demo_repository() -> None:
    report = report_for("moment", "2.29.1", [])

    plan = build_replacement_plan(report, DEMO)

    assert plan.type is FixType.PACKAGE_REPLACEMENT
    assert plan.replacement == "dayjs"
    assert plan.requires_human_review is False
    assert "package.json" in plan.files_to_change
    assert any(path.endswith("dates.js") for path in plan.files_to_change)


def test_replacement_plan_updates_the_manifest_dependencies() -> None:
    report = report_for("moment", "2.29.1", [])

    plan = build_replacement_plan(report, DEMO)
    manifest_change = next(change for change in plan.changes if change.path == "package.json")
    updated = manifest_change.diff

    assert '"moment"' in updated
    assert '"dayjs"' in updated


def test_request_replacement_plan_against_the_demo_repository() -> None:
    report = report_for("request", "2.88.2", [])

    plan = build_replacement_plan(report, DEMO)

    assert plan.replacement == "axios"
    assert plan.requires_human_review is False


def test_replacement_plan_is_refused_when_the_codemod_cannot_help() -> None:
    report = report_for("moment", "2.29.1", [])

    plan = build_replacement_plan(report, DEMO)
    assert plan.requires_human_review is False

    # A repository that uses an unsupported moment API must be refused.
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "package.json").write_text(
            json.dumps({"name": "x", "dependencies": {"moment": "2.29.1"}}), encoding="utf-8"
        )
        (root / "app.js").write_text(
            'const moment = require("moment");\nmoment.locale("de");\n', encoding="utf-8"
        )

        refused = build_replacement_plan(report, root)

    assert refused.requires_human_review is True
    assert refused.changes == []


# ---------------------------------------------------------------------------
# offered options
# ---------------------------------------------------------------------------


def test_offerings_include_a_bump_for_a_fixable_package() -> None:
    report = report_for("lodash", "4.17.15", [vuln("A", Severity.HIGH, ["4.17.21"])])

    options = offer_fix_options(report, DEMO)

    assert any(option.type is FixType.VERSION_BUMP for option in options)
    assert all(not option.requires_human_review for option in options)


def test_offerings_include_a_replacement_for_moment() -> None:
    report = report_for("moment", "2.29.1", [])

    options = offer_fix_options(report, DEMO)

    assert any(option.type is FixType.PACKAGE_REPLACEMENT for option in options)


def test_a_cross_major_fix_is_offered_but_flagged_for_review() -> None:
    report = report_for("jsonwebtoken", "3.2.2", [vuln("A", Severity.CRITICAL, ["9.0.0"])])

    options = offer_fix_options(report, DEMO)

    bump = next(option for option in options if option.type is FixType.VERSION_BUMP)
    assert bump.requires_human_review is True


def test_a_healthy_package_offers_nothing() -> None:
    report = report_for("semver", "7.8.5", [])

    assert offer_fix_options(report, DEMO) == []


# ---------------------------------------------------------------------------
# manifest editing
# ---------------------------------------------------------------------------


def test_manifest_editing_preserves_indentation() -> None:
    from app.fixer import _update_manifest

    original = '{\n  "name": "x",\n  "dependencies": {\n    "moment": "2.29.1"\n  }\n}\n'

    updated = _update_manifest(original, remove="moment", add=("dayjs", "^1.11.23"))

    assert '"dayjs": "^1.11.23"' in updated
    assert '"moment"' not in updated
    assert updated.endswith("\n")
