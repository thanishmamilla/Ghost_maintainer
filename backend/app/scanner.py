"""Repository resolution and dependency discovery.

Turns a user-supplied repository reference into a working copy on disk and a list
of typed direct dependencies. Supports:

* the ``demo`` sentinel, which points at the bundled ``demo-rotten-app``;
* a local filesystem path (development and the demo);
* a public GitHub URL (``https://github.com/owner/repo``), cloned shallowly.

Only direct dependencies are discovered. Transitive resolution is out of scope.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from .config import get_settings
from .models import Dependency

logger = logging.getLogger("ghost_maintainer.scanner")

DEMO_SENTINEL = "demo"

_GITHUB_URL_RE = re.compile(
    r"^https?://(?:www\.)?github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)
_SSH_GITHUB_RE = re.compile(
    r"^git@github\.com:(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?$"
)

MANIFEST_NAME = "package.json"
LOCKFILE_NAMES = ("package-lock.json", "npm-shrinkwrap.json")


class ScanError(Exception):
    """A user-facing error explaining why a repository could not be scanned."""


@dataclass
class ScannedRepository:
    """A resolved working copy plus the direct dependencies it declares."""

    root: Path
    repo_name: str
    repo_url: str
    ref: str | None = None
    base_commit: str | None = None
    dependencies: list[Dependency] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    is_temporary_copy: bool = False

    @property
    def manifest_path(self) -> Path:
        return self.root / MANIFEST_NAME

    def source_root(self) -> Path:
        return self.root


# ---------------------------------------------------------------------------
# repository reference parsing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RepoRef:
    kind: str  # "local" | "github"
    owner: str = ""
    repo: str = ""
    path: Path | None = None
    original: str = ""

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}" if self.owner else self.repo

    @property
    def clone_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}.git"


def parse_repo_ref(repo_input: str) -> RepoRef:
    """Resolve a user-supplied reference into a local path or a GitHub slug.

    Raises:
        ScanError: when the input is empty or cannot be interpreted.
    """
    raw = (repo_input or "").strip()
    if not raw:
        raise ScanError("No repository was supplied.")

    if raw.lower() == DEMO_SENTINEL:
        settings = get_settings()
        demo_path = settings.demo_repo_path.resolve()
        if not demo_path.is_dir():
            raise ScanError(f"The bundled demo repository is missing at {demo_path}.")
        return RepoRef(kind="local", path=demo_path, repo="demo-rotten-app", original=raw)

    if raw.startswith("file://"):
        raw = urlparse(raw).path

    match = _GITHUB_URL_RE.match(raw) or _SSH_GITHUB_RE.match(raw)
    if match:
        owner = match.group("owner")
        repo = match.group("repo")
        if owner.lower() in {"", "github"}:
            raise ScanError(f"{raw!r} does not look like a GitHub repository URL.")
        return RepoRef(kind="github", owner=owner, repo=repo, original=raw)

    # A bare "owner/repo" shorthand is a common and harmless convenience.
    shorthand = re.fullmatch(r"(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)", raw)
    if shorthand:
        return RepoRef(
            kind="github",
            owner=shorthand.group("owner"),
            repo=shorthand.group("repo"),
            original=raw,
        )

    candidate = Path(raw).expanduser()
    if candidate.is_dir():
        return RepoRef(kind="local", path=candidate.resolve(), repo=candidate.name, original=raw)

    if candidate.exists():
        raise ScanError(f"{raw!r} is a file, not a directory containing a {MANIFEST_NAME}.")

    raise ScanError(
        f"{raw!r} is not a GitHub repository URL or an existing local directory. "
        f"Expected https://github.com/owner/repo."
    )


# ---------------------------------------------------------------------------
# manifest and lockfile parsing
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ScanError(f"{path.name} is not valid JSON: {exc.msg} (line {exc.lineno}).") from exc
    except OSError as exc:
        raise ScanError(f"Could not read {path}: {exc.strerror}.") from exc
    if not isinstance(payload, dict):
        raise ScanError(f"{path.name} must contain a JSON object at the top level.")
    return payload


def read_manifest(root: Path) -> dict:
    """Load and validate ``package.json`` from a repository root."""
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise ScanError(f"No {MANIFEST_NAME} found in the repository root. Ghost Maintainer analyses npm projects.")
    manifest = _read_json(manifest_path)
    if "name" not in manifest and not (manifest.get("dependencies") or manifest.get("devDependencies")):
        raise ScanError(f"{MANIFEST_NAME} declares no dependencies to analyse.")
    return manifest


def _normalise_declared_range(value: str) -> str:
    """Reduce a declared range to a comparable string.

    npm's ``workspace:``, ``file:`` and ``link:`` protocols are not registry
    versions, so they are preserved verbatim and flagged by the caller.
    """
    return str(value).strip()


def _lockfile_versions(root: Path) -> tuple[dict[str, str], str | None]:
    """Extract resolved versions from a lockfile.

    Supports lockfile v2/v3 (``packages`` map) and v1 (``dependencies`` map).
    Returns the version map and the lockfile name that was used.
    """
    for name in LOCKFILE_NAMES:
        lock_path = root / name
        if not lock_path.is_file():
            continue
        try:
            payload = _read_json(lock_path)
        except ScanError:
            continue

        versions: dict[str, str] = {}

        packages = payload.get("packages")
        if isinstance(packages, dict):
            for key, entry in packages.items():
                if not isinstance(key, str) or not key.startswith("node_modules/"):
                    continue
                if not isinstance(entry, dict):
                    continue
                package_name = key[len("node_modules/") :]
                # Nested paths describe transitive copies; direct entries sit at the top level.
                if package_name.count("node_modules/") > 0:
                    continue
                version = entry.get("version")
                if isinstance(version, str):
                    versions[package_name] = version

        if not versions:
            legacy = payload.get("dependencies")
            if isinstance(legacy, dict):
                for package_name, entry in legacy.items():
                    if isinstance(entry, dict) and isinstance(entry.get("version"), str):
                        versions[package_name] = entry["version"]

        if versions:
            return versions, name

    return {}, None


def extract_dependencies(manifest: dict, lock_versions: dict[str, str]) -> list[Dependency]:
    """Build typed dependency objects from a manifest plus resolved versions.

    A dependency is only marked ``version_source="lockfile"`` when the lockfile
    actually established the installed version. A declared range such as
    ``^4.17.15`` is *not* an installed version and is never presented as one.
    """
    dependencies: list[Dependency] = []

    for section, is_dev in (("dependencies", False), ("devDependencies", True)):
        declared = manifest.get(section)
        if not isinstance(declared, dict):
            continue

        for name, raw_range in declared.items():
            if not isinstance(name, str) or not name:
                continue
            declared_range = _normalise_declared_range(raw_range if raw_range is not None else "")

            resolved = lock_versions.get(name)
            if resolved:
                version: str | None = resolved
                version_source = "lockfile"
            else:
                version = None
                version_source = "unresolved"

            dependencies.append(
                Dependency(
                    name=name,
                    version=version,
                    version_source=version_source,
                    declared_range=declared_range,
                    is_dev=is_dev,
                )
            )

    dependencies.sort(key=lambda dep: (dep.is_dev, dep.name.lower()))
    return dependencies


# ---------------------------------------------------------------------------
# working copy management
# ---------------------------------------------------------------------------


def _run_git(args: list[str], cwd: Path | None = None, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - fixed argument list, no shell
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        shell=False,
    )


def clone_repository(ref: RepoRef, destination: Path) -> tuple[str | None, str | None]:
    """Shallow-clone a GitHub repository.

    Returns the resolved commit sha and the checked-out branch name.
    """
    result = _run_git(
        ["clone", "--depth", "1", "--single-branch", "--no-tags", ref.clone_url, str(destination)],
        timeout=180,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip().splitlines()
        detail = message[-1] if message else "git clone failed without output"
        raise ScanError(
            f"Could not clone {ref.slug}: {detail}. "
            "Check that the repository exists, is public, and is not empty."
        )

    head = _run_git(["rev-parse", "HEAD"], cwd=destination)
    commit = head.stdout.strip() if head.returncode == 0 and head.stdout.strip() else None

    branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=destination)
    branch_name = branch.stdout.strip() if branch.returncode == 0 and branch.stdout.strip() else None

    return commit, branch_name


def resolve_repository(repo_input: str, *, keep_copy: bool = False) -> ScannedRepository:
    """Resolve a repository reference into a working copy ready for analysis.

    Remote repositories are cloned into a temporary directory that the caller is
    responsible for cleaning up; local directories are used in place.
    """
    ref = parse_repo_ref(repo_input)
    settings = get_settings()
    warnings: list[str] = []

    if ref.kind == "local":
        assert ref.path is not None
        root = ref.path
        if not (root / MANIFEST_NAME).is_file():
            raise ScanError(f"No {MANIFEST_NAME} found in {root}.")
        commit_result = _run_git(["rev-parse", "HEAD"], cwd=root, timeout=15)
        base_commit = commit_result.stdout.strip() if commit_result.returncode == 0 else None
        branch_result = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=root, timeout=15)
        branch = branch_result.stdout.strip() if branch_result.returncode == 0 else None
        is_temporary = False
    else:
        settings.workspaces_dir.mkdir(parents=True, exist_ok=True)
        destination = Path(tempfile.mkdtemp(prefix=f"scan-{uuid.uuid4().hex[:8]}-", dir=settings.workspaces_dir))
        base_commit, branch = clone_repository(ref, destination)
        root = destination
        is_temporary = True
        warnings.append(f"Cloned {ref.slug} into an isolated working copy.")

    manifest = read_manifest(root)
    lock_versions, lockfile_name = _lockfile_versions(root)
    if not lock_versions:
        warnings.append(
            "No package-lock.json was found, so installed versions could not be established. "
            "Declared ranges are shown as unresolved."
        )

    dependencies = extract_dependencies(manifest, lock_versions)

    if not dependencies:
        raise ScanError("The manifest declares no dependencies to analyse.")

    resolved = sum(1 for dep in dependencies if dep.version_source == "lockfile")
    if lock_versions and resolved == 0:
        warnings.append(f"{lockfile_name} did not contain entries for any direct dependency.")

    repo_name = manifest.get("name") or (ref.slug or ref.repo)
    return ScannedRepository(
        root=root,
        repo_name=str(repo_name),
        repo_url=ref.original,
        ref=branch,
        base_commit=base_commit,
        dependencies=dependencies,
        warnings=warnings,
        is_temporary_copy=is_temporary,
    )


def cleanup_repository(scanned: ScannedRepository) -> None:
    """Remove a temporary clone. Local directories are never deleted."""
    if scanned.is_temporary_copy and scanned.root.exists():
        shutil.rmtree(scanned.root, ignore_errors=True)


def scan_dependencies(repo_input: str) -> ScannedRepository:
    """Convenience wrapper: resolve a repository and return its dependencies."""
    return resolve_repository(repo_input)
