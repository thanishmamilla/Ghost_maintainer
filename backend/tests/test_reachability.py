"""Reachability tests: import patterns, exclusions and classification."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models import Dependency, ReachabilityClass, Severity, Vulnerability
from app.reachability import analyse, collect_source_files, package_root, scan_source


def dep(name: str) -> Dependency:
    return Dependency(name=name, version="1.0.0", version_source="lockfile", declared_range="1.0.0")


def write(root: Path, relative: str, content: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# package name resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("specifier", "expected"),
    [
        ("lodash", "lodash"),
        ("lodash/fp", "lodash"),
        ("@scope/pkg", "@scope/pkg"),
        ("@scope/pkg/sub", "@scope/pkg"),
        ("./relative", None),
        ("../parent", None),
        ("/absolute", None),
        ("node:fs", None),
    ],
)
def test_package_root_extraction(specifier: str, expected: str | None) -> None:
    assert package_root(specifier) == expected


# ---------------------------------------------------------------------------
# import detection
# ---------------------------------------------------------------------------


def test_default_import_is_detected(tmp_path: Path) -> None:
    write(tmp_path, "src/a.js", 'import moment from "moment";\n')

    outcome = scan_source(tmp_path)

    assert len(outcome.matches) == 1
    assert outcome.matches[0].kind == "import"
    assert outcome.matches[0].line == 1
    assert outcome.matches[0].members == ["moment"]


def test_named_import_members_are_recorded(tmp_path: Path) -> None:
    write(tmp_path, "src/a.js", 'import { get, post } from "request";\n')

    outcome = scan_source(tmp_path)

    assert outcome.matches[0].members == ["get", "post"]


def test_require_is_detected(tmp_path: Path) -> None:
    write(tmp_path, "src/a.js", 'const _ = require("lodash");\n')

    outcome = scan_source(tmp_path)

    assert outcome.matches[0].kind == "require"
    assert outcome.matches[0].members == ["_"]


def test_destructured_require_members_are_recorded(tmp_path: Path) -> None:
    write(tmp_path, "src/a.js", 'const { parse, help } = require("minimist");\n')

    outcome = scan_source(tmp_path)

    assert set(outcome.matches[0].members) == {"parse", "help"}


def test_bare_require_is_detected(tmp_path: Path) -> None:
    write(tmp_path, "src/a.js", 'require("moment");\n')

    outcome = scan_source(tmp_path)

    assert outcome.matches[0].kind == "require"


def test_dynamic_import_is_detected(tmp_path: Path) -> None:
    write(tmp_path, "src/a.js", 'const mod = await import("some-package");\n')

    outcome = scan_source(tmp_path)

    assert outcome.matches[0].kind == "dynamic_import"


def test_side_effect_import_is_detected(tmp_path: Path) -> None:
    write(tmp_path, "src/a.js", 'import "polyfill";\n')

    outcome = scan_source(tmp_path)

    assert outcome.matches[0].kind == "side_effect"


def test_re_export_is_detected(tmp_path: Path) -> None:
    write(tmp_path, "src/a.js", 'export { helper } from "some-package";\n')

    outcome = scan_source(tmp_path)

    assert len(outcome.matches) == 1


def test_relative_imports_are_ignored(tmp_path: Path) -> None:
    write(tmp_path, "src/a.js", 'import thing from "./local";\n')

    outcome = scan_source(tmp_path)

    assert outcome.matches == []


def test_line_numbers_are_correct(tmp_path: Path) -> None:
    write(tmp_path, "src/a.js", "// line 1\n// line 2\nconst _ = require('lodash');\n")

    outcome = scan_source(tmp_path)

    assert outcome.matches[0].line == 3


# ---------------------------------------------------------------------------
# exclusions
# ---------------------------------------------------------------------------


def test_node_modules_is_excluded(tmp_path: Path) -> None:
    write(tmp_path, "node_modules/pkg/index.js", 'const x = require("lodash");\n')

    assert collect_source_files(tmp_path) == []


def test_build_output_is_excluded(tmp_path: Path) -> None:
    write(tmp_path, "dist/bundle.js", 'const x = require("lodash");\n')
    write(tmp_path, "build/bundle.js", 'const x = require("lodash");\n')

    assert collect_source_files(tmp_path) == []


def test_generated_files_are_excluded(tmp_path: Path) -> None:
    write(tmp_path, "src/__generated__/api.js", 'const x = require("lodash");\n')

    assert collect_source_files(tmp_path) == []


def test_minified_files_are_excluded(tmp_path: Path) -> None:
    write(tmp_path, "static/vendor.min.js", 'const x = require("lodash");\n')

    assert collect_source_files(tmp_path) == []


def test_unsupported_extensions_are_skipped(tmp_path: Path) -> None:
    write(tmp_path, "styles.css", 'body { content: "lodash"; }')
    write(tmp_path, "data.json", '{"lodash": 1}')

    assert collect_source_files(tmp_path) == []


def test_all_supported_extensions_are_scanned(tmp_path: Path) -> None:
    for extension in ["js", "jsx", "ts", "tsx", "mjs", "cjs"]:
        write(tmp_path, f"src/file.{extension}", 'require("pkg");\n')

    assert len(collect_source_files(tmp_path)) == 6


# ---------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------


def test_imported_dependency_is_observed(tmp_path: Path) -> None:
    write(tmp_path, "src/a.js", 'const _ = require("lodash");\n')

    results = analyse(tmp_path, [dep("lodash")])

    assert results["lodash"].classification is ReachabilityClass.OBSERVED
    assert results["lodash"].matches
    assert results["lodash"].matches[0].file == "src/a.js"


def test_unreferenced_dependency_is_not_observed(tmp_path: Path) -> None:
    write(tmp_path, "src/a.js", 'const _ = require("lodash");\n')

    results = analyse(tmp_path, [dep("jsonwebtoken")])

    assert results["jsonwebtoken"].classification is ReachabilityClass.NOT_OBSERVED
    assert results["jsonwebtoken"].matches == []


def test_unobserved_result_explains_that_absence_is_not_proof(tmp_path: Path) -> None:
    write(tmp_path, "src/a.js", 'const _ = require("lodash");\n')

    results = analyse(tmp_path, [dep("jsonwebtoken")])

    explanation = results["jsonwebtoken"].explanation
    assert "does not prove" in explanation
    assert "transitively" in explanation
    assert results["jsonwebtoken"].limitation


def test_repository_without_source_files_is_unknown(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("no source here", encoding="utf-8")

    results = analyse(tmp_path, [dep("lodash")])

    assert results["lodash"].classification is ReachabilityClass.UNKNOWN


def test_scanned_file_count_is_reported(tmp_path: Path) -> None:
    write(tmp_path, "src/a.js", 'require("a");\n')
    write(tmp_path, "src/b.js", 'require("b");\n')

    results = analyse(tmp_path, [dep("a")])

    assert results["a"].scanned_files == 2


def test_subpath_imports_are_attributed_to_the_package(tmp_path: Path) -> None:
    write(tmp_path, "src/a.js", 'import fp from "lodash/fp";\n')

    results = analyse(tmp_path, [dep("lodash")])

    assert results["lodash"].classification is ReachabilityClass.OBSERVED


def test_scoped_packages_are_matched(tmp_path: Path) -> None:
    write(tmp_path, "src/a.js", 'import x from "@scope/pkg/sub";\n')

    results = analyse(tmp_path, [dep("@scope/pkg")])

    assert results["@scope/pkg"].classification is ReachabilityClass.OBSERVED


# ---------------------------------------------------------------------------
# vulnerability-aware reachability
# ---------------------------------------------------------------------------


def test_advisory_without_affected_functions_leaves_function_reachability_unknown(tmp_path: Path) -> None:
    write(tmp_path, "src/a.js", 'const _ = require("lodash");\n')
    vulnerabilities = {"lodash": [Vulnerability(id="GHSA-1", severity=Severity.HIGH)]}

    results = analyse(tmp_path, [dep("lodash")], vulnerabilities)

    assert "unknown" in results["lodash"].explanation.lower()


def test_advisory_named_functions_are_compared_with_usage(tmp_path: Path) -> None:
    write(tmp_path, "src/a.js", 'const { merge } = require("lodash");\n')
    vulnerabilities = {
        "lodash": [Vulnerability(id="GHSA-1", severity=Severity.HIGH, affected_functions=["merge"])]
    }

    results = analyse(tmp_path, [dep("lodash")], vulnerabilities)

    assert "merge" in results["lodash"].explanation
    assert "heuristic" in results["lodash"].explanation.lower()


def test_advisory_functions_that_do_not_overlap_are_reported(tmp_path: Path) -> None:
    write(tmp_path, "src/a.js", 'const { merge } = require("lodash");\n')
    vulnerabilities = {
        "lodash": [Vulnerability(id="GHSA-1", severity=Severity.HIGH, affected_functions=["template"])]
    }

    results = analyse(tmp_path, [dep("lodash")], vulnerabilities)

    assert "none of them match" in results["lodash"].explanation
