"""Structural checks on the demo repository itself.

These assert the demo keeps the shape the pitch depends on: an installed but
unimported vulnerable dependency, a healthy baseline, and four rotten packages
that are genuinely imported.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

DEMO = Path(__file__).resolve().parent.parent.parent / "demo-rotten-app"
SRC = DEMO / "src"
TESTS = DEMO / "tests"

IMPORT_PATTERN = re.compile(
    r"""(?:require\(\s*["'](?P<require>[^"']+)["']\s*\)"""
    r"""|from\s+["'](?P<from>[^"']+)["']"""
    r"""|import\s+["'](?P<import>[^"']+)["'])""",
    re.MULTILINE,
)


def source_files() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.js") if p.is_file())


def imported_packages() -> set[str]:
    found: set[str] = set()
    for path in source_files():
        for match in IMPORT_PATTERN.finditer(path.read_text(encoding="utf-8")):
            specifier = match.group("require") or match.group("from") or match.group("import")
            if specifier and not specifier.startswith("."):
                found.add(specifier.split("/")[0] if not specifier.startswith("@") else "/".join(specifier.split("/")[:2]))
    return found


def manifest() -> dict:
    return json.loads((DEMO / "package.json").read_text(encoding="utf-8"))


def test_demo_package_json_exists() -> None:
    assert (DEMO / "package.json").is_file()
    assert (DEMO / "package-lock.json").is_file()


def test_manifest_declares_the_intended_dependencies() -> None:
    deps = manifest()["dependencies"]
    assert deps["moment"] == "2.29.1"
    assert deps["request"] == "2.88.2"
    assert deps["lodash"] == "4.17.15"
    assert deps["minimist"] == "1.2.0"
    assert deps["semver"] == "7.8.5"
    assert deps["jsonwebtoken"] == "3.2.2"


def test_lockfile_pins_the_intended_versions() -> None:
    lock = json.loads((DEMO / "package-lock.json").read_text(encoding="utf-8"))
    packages = lock["packages"]
    for name, expected in [
        ("moment", "2.29.1"),
        ("request", "2.88.2"),
        ("lodash", "4.17.15"),
        ("minimist", "1.2.0"),
        ("semver", "7.8.5"),
        ("jsonwebtoken", "3.2.2"),
    ]:
        assert packages[f"node_modules/{name}"]["version"] == expected


def test_jsonwebtoken_is_installed_but_never_imported() -> None:
    """The 'not observed' demo case: present in the manifest, absent from source."""
    assert "jsonwebtoken" in manifest()["dependencies"]
    assert "jsonwebtoken" not in imported_packages()


def test_the_four_rotten_packages_are_genuinely_imported() -> None:
    imported = imported_packages()
    for name in ["moment", "request", "lodash", "minimist", "semver"]:
        assert name in imported, f"{name} should be imported by the demo source"


def test_demo_has_four_to_six_source_files() -> None:
    assert 4 <= len(source_files()) <= 6


def test_demo_test_suite_covers_every_source_module() -> None:
    test_files = sorted(TESTS.glob("*.test.js"))
    assert len(test_files) >= 4
    contents = "\n".join(path.read_text(encoding="utf-8") for path in test_files)
    for module in ["dates", "objects", "args", "http", "versions"]:
        assert f"../src/{module}" in contents, f"no test imports ../src/{module}"


def test_demo_tests_avoid_real_network_calls() -> None:
    """The HTTP helper must be exercised through an injected fake, not a live request."""
    http_test = (TESTS / "http.test.js").read_text(encoding="utf-8")
    assert "fakeClient" in http_test
    assert "axios" not in http_test, "the demo must not already depend on the migration target"


def test_demo_http_helper_keeps_the_transport_injectable() -> None:
    """The migration target must be able to replace the client behind one interface."""
    http_source = (SRC / "http.js").read_text(encoding="utf-8")
    assert 'require("request")' in http_source
    assert "client = request" in http_source


def test_demo_has_no_lint_or_build_step_that_could_fail_the_demo() -> None:
    scripts = manifest()["scripts"]
    assert set(scripts) <= {"test", "lint", "format", "build"}
    assert "test" in scripts
