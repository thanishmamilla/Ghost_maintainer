"""Scanner tests: manifest parsing, lockfile resolution and error handling."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models import Dependency
from app.scanner import (
    ScanError,
    extract_dependencies,
    parse_repo_ref,
    read_manifest,
    resolve_repository,
)
from app.semver_utils import Version, compare, highest_version, is_at_least_major_behind


def write_manifest(root: Path, manifest: dict) -> None:
    (root / "package.json").write_text(json.dumps(manifest), encoding="utf-8")


def write_lockfile(root: Path, lock: dict) -> None:
    (root / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")


# ---------------------------------------------------------------------------
# manifest parsing
# ---------------------------------------------------------------------------


def test_extract_dependencies_splits_runtime_and_dev(tmp_path: Path) -> None:
    manifest = {
        "name": "sample",
        "dependencies": {"lodash": "^4.17.15"},
        "devDependencies": {"jest": "^30.0.0"},
    }

    deps = extract_dependencies(manifest, {})

    by_name = {dep.name: dep for dep in deps}
    assert by_name["lodash"].is_dev is False
    assert by_name["jest"].is_dev is True
    assert by_name["lodash"].declared_range == "^4.17.15"


def test_extract_dependencies_without_a_lockfile_leaves_versions_unresolved() -> None:
    deps = extract_dependencies({"dependencies": {"lodash": "^4.17.15"}}, {})

    dep = deps[0]
    assert dep.version is None
    assert dep.version_source == "unresolved"
    # The declared range must not be presented as an installed version.
    assert dep.declared_range == "^4.17.15"


def test_exact_declared_range_is_not_treated_as_a_locked_version() -> None:
    deps = extract_dependencies({"dependencies": {"left-pad": "1.3.0"}}, {})

    assert deps[0].version is None
    assert deps[0].version_source == "unresolved"


def test_dependencies_are_sorted_runtime_first(tmp_path: Path) -> None:
    manifest = {
        "dependencies": {"zeta": "1.0.0", "alpha": "1.0.0"},
        "devDependencies": {"beta": "1.0.0"},
    }

    deps = extract_dependencies(manifest, {})
    assert [dep.name for dep in deps] == ["alpha", "zeta", "beta"]


# ---------------------------------------------------------------------------
# lockfile resolution
# ---------------------------------------------------------------------------


def test_lockfile_v3_resolves_installed_versions(tmp_path: Path) -> None:
    write_manifest(tmp_path, {"name": "sample", "dependencies": {"lodash": "^4.17.15"}})
    write_lockfile(
        tmp_path,
        {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "sample"},
                "node_modules/lodash": {"version": "4.17.15"},
                "node_modules/left-pad": {"version": "1.3.0"},
            },
        },
    )

    scanned = resolve_repository(str(tmp_path))

    dep = next(d for d in scanned.dependencies if d.name == "lodash")
    assert dep.version == "4.17.15"
    assert dep.version_source == "lockfile"


def test_legacy_lockfile_v1_resolves_installed_versions(tmp_path: Path) -> None:
    write_manifest(tmp_path, {"name": "sample", "dependencies": {"lodash": "^4.17.15"}})
    write_lockfile(
        tmp_path,
        {
            "lockfileVersion": 1,
            "dependencies": {"lodash": {"version": "4.17.15"}},
        },
    )

    scanned = resolve_repository(str(tmp_path))
    dep = next(d for d in scanned.dependencies if d.name == "lodash")

    assert dep.version == "4.17.15"
    assert dep.version_source == "lockfile"


def test_nested_transitive_entries_do_not_override_direct_versions(tmp_path: Path) -> None:
    write_manifest(tmp_path, {"name": "sample", "dependencies": {"lodash": "^4.17.15"}})
    write_lockfile(
        tmp_path,
        {
            "lockfileVersion": 3,
            "packages": {
                "node_modules/lodash": {"version": "4.17.15"},
                "node_modules/parent/node_modules/lodash": {"version": "3.10.1"},
            },
        },
    )

    scanned = resolve_repository(str(tmp_path))
    dep = next(d for d in scanned.dependencies if d.name == "lodash")

    assert dep.version == "4.17.15"


def test_missing_lockfile_produces_a_warning_not_an_error(tmp_path: Path) -> None:
    write_manifest(tmp_path, {"name": "sample", "dependencies": {"lodash": "^4.17.15"}})

    scanned = resolve_repository(str(tmp_path))

    assert any("package-lock.json" in warning for warning in scanned.warnings)
    assert scanned.dependencies[0].version is None


def test_dependency_missing_from_lockfile_stays_unresolved(tmp_path: Path) -> None:
    write_manifest(tmp_path, {"name": "sample", "dependencies": {"lodash": "^4.17.15", "ghost": "^1.0.0"}})
    write_lockfile(tmp_path, {"lockfileVersion": 3, "packages": {"node_modules/lodash": {"version": "4.17.15"}}})

    scanned = resolve_repository(str(tmp_path))
    ghost = next(d for d in scanned.dependencies if d.name == "ghost")

    assert ghost.version is None
    assert ghost.version_source == "unresolved"


# ---------------------------------------------------------------------------
# error handling
# ---------------------------------------------------------------------------


def test_invalid_url_raises_a_useful_error() -> None:
    with pytest.raises(ScanError) as excinfo:
        parse_repo_ref("not-a-repo")

    assert "GitHub repository URL" in str(excinfo.value)


def test_empty_input_raises_a_useful_error() -> None:
    with pytest.raises(ScanError) as excinfo:
        parse_repo_ref("   ")

    assert "No repository" in str(excinfo.value)


def test_missing_manifest_raises_a_useful_error(tmp_path: Path) -> None:
    with pytest.raises(ScanError) as excinfo:
        resolve_repository(str(tmp_path))

    assert "package.json" in str(excinfo.value)


def test_malformed_manifest_raises_a_useful_error(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{ not json", encoding="utf-8")

    with pytest.raises(ScanError) as excinfo:
        read_manifest(tmp_path)

    assert "not valid JSON" in str(excinfo.value)


def test_manifest_without_dependencies_raises(tmp_path: Path) -> None:
    write_manifest(tmp_path, {"name": "empty", "version": "1.0.0"})

    with pytest.raises(ScanError) as excinfo:
        resolve_repository(str(tmp_path))

    assert "no dependencies" in str(excinfo.value).lower()


def test_a_file_path_is_rejected_clearly(tmp_path: Path) -> None:
    target = tmp_path / "package.json"
    target.write_text("{}", encoding="utf-8")

    with pytest.raises(ScanError) as excinfo:
        parse_repo_ref(str(target))

    assert "not a directory" in str(excinfo.value)


# ---------------------------------------------------------------------------
# reference parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "https://github.com/owner/repo",
        "https://github.com/owner/repo/",
        "https://github.com/owner/repo.git",
        "https://www.github.com/owner/repo",
        "http://github.com/owner/repo",
        "owner/repo",
    ],
)
def test_github_references_are_recognised(value: str) -> None:
    ref = parse_repo_ref(value)

    assert ref.kind == "github"
    assert ref.owner == "owner"
    assert ref.repo == "repo"


def test_demo_sentinel_resolves_to_the_bundled_repository() -> None:
    ref = parse_repo_ref("demo")

    assert ref.kind == "local"
    assert ref.path is not None
    assert (ref.path / "package.json").is_file()


def test_local_directory_is_recognised(tmp_path: Path) -> None:
    ref = parse_repo_ref(str(tmp_path))

    assert ref.kind == "local"
    assert ref.path == tmp_path.resolve()


# ---------------------------------------------------------------------------
# semver helpers used by later steps
# ---------------------------------------------------------------------------


def test_version_ordering() -> None:
    assert compare("1.2.0", "1.10.0") == -1
    assert compare("2.0.0", "1.9.9") == 1
    assert compare("1.0.0", "1.0.0") == 0


def test_prerelease_sorts_before_release() -> None:
    assert Version.parse("1.0.0-rc.1") < Version.parse("1.0.0")


def test_major_lag_detection() -> None:
    assert is_at_least_major_behind("1.2.0", "2.0.0") is True
    assert is_at_least_major_behind("1.2.0", "1.9.0") is False
    assert is_at_least_major_behind("not-a-version", "2.0.0") is False


def test_highest_version_ignores_prereleases() -> None:
    assert highest_version(["1.0.0", "2.0.0-rc.1", "1.5.0"]) == "1.5.0"
