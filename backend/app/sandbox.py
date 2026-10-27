"""Isolated execution of untrusted repository code.

Two runners are supported:

* **Docker** — a disposable container with a memory cap, a CPU cap, no host
  mounts beyond the temporary workspace, and no access to the backend's
  environment or credentials. Network access is granted for dependency
  installation and revoked for the test run itself.
* **Local restricted** — a deliberately honest fallback for environments without
  a container runtime. It runs on the host with a scrubbed environment, an
  argument list rather than a shell, lifecycle scripts disabled, a wall-clock
  timeout and a process-tree kill. It is **not** a security boundary and the UI
  says so. It requires explicit opt-in through ``ALLOW_UNTRUSTED_LOCAL_EXECUTION``.

Neither runner is used to execute anything except the repository's declared test
command and its dependency installation.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .config import get_settings
from .models import FixPlan, SandboxResult, SandboxStatus

logger = logging.getLogger("ghost_maintainer.sandbox")

DOCKER_IMAGE = "node:22-alpine"
INSTALL_TIMEOUT_SECONDS = 420
OUTPUT_TAIL_CHARS = 4000

# Environment variables that must never reach repository code.
SECRET_KEYS = (
    "GITHUB_TOKEN",
    "LLM_API_KEY",
    "LLM_MODEL",
    "LLM_BASE_URL",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "GOOGLE_API_KEY",
)

# Only these are passed through to the local runner.
SAFE_ENV_KEYS = ("PATH", "HOME", "SystemRoot", "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "ComSpec", "PATHEXT")

_LOCKFILES = ("package-lock.json", "npm-shrinkwrap.json")


@dataclass
class CommandOutcome:
    """The result of one executed command."""

    command: list[str]
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    timed_out: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and self.error is None

    @property
    def output_tail(self) -> str:
        combined = self.stdout.strip()
        if self.stderr.strip():
            combined = f"{combined}\n{self.stderr.strip()}" if combined else self.stderr.strip()
        if len(combined) > OUTPUT_TAIL_CHARS:
            return "…\n" + combined[-OUTPUT_TAIL_CHARS:]
        return combined


@dataclass
class SandboxOptions:
    """Configuration for one sandbox run."""

    timeout_seconds: int = 180
    install_timeout_seconds: int = INSTALL_TIMEOUT_SECONDS
    mode: str = "auto"
    test_command: list[str] = field(default_factory=lambda: ["npm", "test"])
    allow_network_for_install: bool = True
    skip_install: bool = False
    prefill_node_modules: bool = True
    """Reuse the working copy's installed dependencies instead of reinstalling.

    A cold `npm ci` dominates the runtime of a demo fix (roughly 70 seconds for
    the demo repository) and changes nothing about the result. When the source
    directory already has `node_modules` and a lockfile, copying it makes the
    baseline identical to the developer's own checkout and reduces the run to a
    few seconds. Cloned repositories have no `node_modules`, so they still get a
    full install.
    """


def _scrubbed_env() -> dict[str, str]:
    """A minimal environment with every credential removed."""
    environment: dict[str, str] = {}
    for key in SAFE_ENV_KEYS:
        value = os.environ.get(key)
        if value:
            environment[key] = value
    environment.setdefault("PATH", os.environ.get("PATH", ""))
    environment["CI"] = "true"
    environment["NODE_ENV"] = "test"
    environment["npm_config_audit"] = "false"
    environment["npm_config_fund"] = "false"
    # Lifecycle scripts are executable untrusted code; disable them locally.
    environment["npm_config_ignore_scripts"] = "true"
    for key in SECRET_KEYS:
        environment.pop(key, None)
    return environment


async def _kill_process_tree(process: asyncio.subprocess.Process) -> None:
    """Terminate a process and every child it spawned."""
    if process.returncode is not None:
        return
    try:
        if sys.platform == "win32":
            await asyncio.create_subprocess_exec(
                "taskkill", "/PID", str(process.pid), "/T", "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        else:
            process.terminate()
    except (ProcessLookupError, OSError):
        pass

    try:
        await asyncio.wait_for(process.wait(), timeout=10)
    except asyncio.TimeoutError:
        try:
            process.kill()
        except (ProcessLookupError, OSError):
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:  # pragma: no cover - defensive
            logger.error("Could not terminate process %s", process.pid)


def _resolve_executable(name: str) -> str:
    """Resolve a command name to a real file path.

    On Windows, `npm` is `npm.cmd`; spawning it by bare name fails without a shell.
    """
    if not name or os.path.isabs(name) or os.sep in name or (os.altsep and os.altsep in name):
        return name
    resolved = shutil.which(name)
    return resolved or name


async def execute(
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
    env: dict[str, str] | None = None,
) -> CommandOutcome:
    """Run a command with a hard timeout and a full process-tree kill."""
    started = time.perf_counter()
    resolved = [_resolve_executable(command[0]), *command[1:]] if command else command
    try:
        process = await asyncio.create_subprocess_exec(
            *resolved,
            cwd=str(cwd),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return CommandOutcome(
            command=command,
            exit_code=None,
            duration_seconds=time.perf_counter() - started,
            error=f"The command {command[0]!r} was not found on PATH.",
        )
    except OSError as exc:
        return CommandOutcome(
            command=command,
            exit_code=None,
            duration_seconds=time.perf_counter() - started,
            error=f"Could not start {command[0]!r}: {exc.strerror}.",
        )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout)
        return CommandOutcome(
            command=command,
            exit_code=process.returncode,
            stdout=stdout_bytes.decode("utf-8", "replace"),
            stderr=stderr_bytes.decode("utf-8", "replace"),
            duration_seconds=time.perf_counter() - started,
        )
    except asyncio.TimeoutError:
        await _kill_process_tree(process)
        return CommandOutcome(
            command=command,
            exit_code=None,
            duration_seconds=time.perf_counter() - started,
            timed_out=True,
            error=f"The command exceeded its {timeout}s limit and was terminated.",
        )


# ---------------------------------------------------------------------------
# docker availability
# ---------------------------------------------------------------------------

_docker_available: bool | None = None


def docker_available(refresh: bool = False) -> bool:
    """Whether a Docker daemon is reachable. Probed once per process."""
    global _docker_available
    if _docker_available is not None and not refresh:
        return _docker_available

    docker = shutil.which("docker")
    if not docker:
        _docker_available = False
        return False
    try:
        result = subprocess.run(  # noqa: S603
            [docker, "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=15,
            shell=False,
        )
        _docker_available = result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        _docker_available = False
    return _docker_available


def resolve_mode(requested: str | None = None) -> str:
    """Pick the execution mode, honouring an explicit request where possible."""
    settings = get_settings()
    requested = (requested or settings.sandbox_mode or "auto").lower()
    if requested == "docker":
        return "docker"
    if requested == "local_restricted":
        return "local_restricted"
    return "docker" if docker_available() else "local_restricted"


# ---------------------------------------------------------------------------
# runners
# ---------------------------------------------------------------------------


async def run_local(command: list[str], workspace: Path, timeout: int) -> CommandOutcome:
    """Run a command on the host with a scrubbed environment."""
    settings = get_settings()
    if not settings.allow_untrusted_local_execution:
        return CommandOutcome(
            command=command,
            exit_code=None,
            error=(
                "Local execution of untrusted repository code is disabled. Start Docker Desktop, or set "
                "ALLOW_UNTRUSTED_LOCAL_EXECUTION=true to accept the risk in a throwaway environment."
            ),
        )
    return await execute(command, cwd=workspace, timeout=timeout, env=_scrubbed_env())


async def run_docker(
    command: list[str],
    workspace: Path,
    timeout: int,
    *,
    network: bool,
) -> CommandOutcome:
    """Run a command inside a disposable container."""
    docker = shutil.which("docker")
    if not docker:
        return CommandOutcome(command=command, exit_code=None, error="Docker is not installed.")

    container_command = [
        docker,
        "run",
        "--rm",
        "--memory",
        "2g",
        "--cpus",
        "2",
        "--pids-limit",
        "512",
        "--network",
        "bridge" if network else "none",
        # Mount only the temporary workspace, read-write.
        "-v",
        f"{workspace}:/workspace",
        "-w",
        "/workspace",
        # No credentials are passed into the container.
        "-e",
        "CI=true",
        "-e",
        "NODE_ENV=test",
        DOCKER_IMAGE,
        *command,
    ]
    return await execute(container_command, cwd=workspace, timeout=timeout)


async def install_dependencies(
    workspace: Path,
    mode: str,
    *,
    timeout: int,
    ignore_scripts: bool,
    frozen: bool = True,
) -> CommandOutcome:
    """Install dependencies in the workspace.

    ``frozen`` uses a reproducible install from the lockfile. It must be off after
    a plan changes the manifest, because the lockfile is then out of date and a
    frozen install would refuse to proceed.
    """
    has_lockfile = any((workspace / name).is_file() for name in _LOCKFILES)
    use_ci = frozen and has_lockfile
    command = ["npm", "ci" if use_ci else "install", "--no-audit", "--no-fund"]
    if ignore_scripts:
        command.append("--ignore-scripts")

    if mode == "docker":
        return await run_docker(command, workspace, timeout, network=True)
    return await run_local(command, workspace, timeout)


# ---------------------------------------------------------------------------
# workspace handling
# ---------------------------------------------------------------------------


def _copy_tree_fast(source: Path, destination: Path) -> None:
    """Copy a tree using hard links where the filesystem allows it.

    `node_modules` for the demo repository is ~8,000 files; a plain copy takes
    around 40 seconds, which would make a live fix far too slow to demo. Hard
    links share the file data, so the copy takes a few seconds. npm replaces
    package directories when it installs rather than editing files in place, so
    the source checkout is not modified. Filesystems without hard-link support
    fall back to a normal copy.
    """

    def link_or_copy(src: str, dst: str) -> str:
        try:
            os.link(src, dst)
            return dst
        except OSError:
            return str(shutil.copy2(src, dst))

    shutil.copytree(source, destination, copy_function=link_or_copy)


def prepare_workspace(source: Path, *, label: str = "fix", include_node_modules: bool = False) -> Path:
    """Create a fresh working copy so the original is never modified."""
    settings = get_settings()
    settings.workspaces_dir.mkdir(parents=True, exist_ok=True)
    destination = settings.workspaces_dir / f"{label}-{uuid.uuid4().hex[:8]}"

    ignored = ["node_modules", "dist", "build", "coverage", ".venv", "__pycache__", ".workspaces"]

    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns(*ignored),
        dirs_exist_ok=False,
    )

    if include_node_modules and (source / "node_modules").is_dir():
        _copy_tree_fast(source / "node_modules", destination / "node_modules")

    return destination


def apply_plan(plan: FixPlan, workspace: Path) -> list[str]:
    """Write the plan's changed files into the workspace.

    Returns the paths that were written.
    """
    root = workspace.resolve()
    written: list[str] = []
    for change in plan.changes:
        target = (root / change.path).resolve()
        if root not in target.parents and target != root:
            raise ValueError(f"Refusing to write outside the workspace: {change.path}")
        if not change.content:
            raise ValueError(f"The plan carries no content for {change.path}.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(change.content, encoding="utf-8")
        written.append(change.path)
    return written


def workspace_diff(workspace: Path, paths: list[str], originals: dict[str, str]) -> str:
    """Unified diff for the files a plan changed, compared against their originals."""
    from .codemods import unified_diff

    pieces: list[str] = []
    for path in paths:
        target = workspace / path
        if not target.is_file():
            continue
        updated = target.read_text(encoding="utf-8")
        original = originals.get(path, "")
        diff = unified_diff(path, original, updated)
        if diff:
            pieces.append(diff)
    return "".join(pieces)


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


async def run_sandbox(
    plan: FixPlan,
    source_root: Path,
    *,
    options: SandboxOptions | None = None,
) -> SandboxResult:
    """Run the full baseline -> patch -> retest pipeline in an isolated workspace.

    The original repository is never modified: everything happens in a copy.
    """
    settings = get_settings()
    options = options or SandboxOptions(timeout_seconds=settings.sandbox_timeout_seconds)
    mode = resolve_mode(options.mode)
    started = time.perf_counter()

    result = SandboxResult(
        status=SandboxStatus.ERROR,
        mode=mode,  # type: ignore[arg-type]
        command=" ".join(options.test_command),
    )

    # Reuse installed dependencies when the working copy already has them.
    reusable_modules = (
        options.prefill_node_modules
        and not options.skip_install
        and (source_root / "node_modules").is_dir()
        and any((source_root / name).is_file() for name in _LOCKFILES)
    )

    try:
        workspace = prepare_workspace(source_root, include_node_modules=reusable_modules)
    except OSError as exc:
        result.error = f"Could not prepare the isolated workspace: {exc}."
        return result

    result.workspace_path = str(workspace)

    if reusable_modules:
        result.notes.append(
            "Reused the working copy's installed dependencies, so the baseline matches the developer's "
            "own checkout instead of installing from scratch."
        )

    # Record the original bodies so the diff is exact.
    originals: dict[str, str] = {}
    for change in plan.changes:
        path = workspace / change.path
        originals[change.path] = path.read_text(encoding="utf-8") if path.is_file() else ""

    # 1. Baseline install.
    if not options.skip_install and not reusable_modules:
        install = await install_dependencies(
            workspace,
            mode,
            timeout=options.install_timeout_seconds,
            ignore_scripts=(mode == "local_restricted"),
        )
        if not install.ok:
            result.status = SandboxStatus.TIMEOUT if install.timed_out else SandboxStatus.ERROR
            result.error = install.error or f"Dependency installation failed (exit code {install.exit_code})."
            result.baseline_output_tail = install.output_tail
            result.duration_seconds = time.perf_counter() - started
            return result

    # 2. Baseline tests, before anything is changed.
    baseline = await run_tests(workspace, mode, options)
    result.baseline_exit_code = baseline.exit_code
    result.baseline_output_tail = baseline.output_tail
    result.baseline_passed = baseline.ok

    if baseline.timed_out:
        result.status = SandboxStatus.TIMEOUT
        result.error = baseline.error
        result.duration_seconds = time.perf_counter() - started
        return result

    if not baseline.ok:
        result.status = SandboxStatus.BASELINE_FAILED
        result.error = (
            "The repository's own test suite fails before any change. A later result cannot be "
            "attributed to the proposed fix."
        )
        result.duration_seconds = time.perf_counter() - started
        return result

    # 3. Apply the approved plan.
    try:
        written = apply_plan(plan, workspace)
    except (OSError, ValueError) as exc:
        result.status = SandboxStatus.ERROR
        result.error = f"Could not apply the fix: {exc}"
        result.duration_seconds = time.perf_counter() - started
        return result

    result.files_changed = written

    # 4. Reinstall: the plan may have changed the manifest, so the lockfile is stale.
    if not options.skip_install:
        reinstall = await install_dependencies(
            workspace,
            mode,
            timeout=options.install_timeout_seconds,
            ignore_scripts=(mode == "local_restricted"),
            frozen=False,
        )
        if not reinstall.ok:
            result.status = SandboxStatus.TIMEOUT if reinstall.timed_out else SandboxStatus.ERROR
            result.error = reinstall.error or f"Reinstalling after the fix failed (exit code {reinstall.exit_code})."
            result.test_output_tail = reinstall.output_tail
            result.duration_seconds = time.perf_counter() - started
            return result

    # 5. Post-fix tests.
    after = await run_tests(workspace, mode, options)
    result.after_exit_code = after.exit_code
    result.test_output_tail = after.output_tail
    result.after_passed = after.ok

    # 6. Diff.
    result.diff = workspace_diff(workspace, written, originals)
    result.duration_seconds = time.perf_counter() - started

    if after.timed_out:
        result.status = SandboxStatus.TIMEOUT
        result.error = after.error
        return result

    if not after.ok:
        result.status = SandboxStatus.NEEDS_HUMAN_REVIEW
        result.error = (
            "The test suite fails after the fix. The change is not proposed as a pull request; the "
            "failure output is preserved for review."
        )
        return result

    result.status = SandboxStatus.PASSED
    return result


async def run_tests(workspace: Path, mode: str, options: SandboxOptions) -> CommandOutcome:
    """Run the repository's test command with network access revoked where possible."""
    if mode == "docker":
        return await run_docker(options.test_command, workspace, options.timeout_seconds, network=False)
    return await run_local(options.test_command, workspace, options.timeout_seconds)

def prune_workspaces(root: Path | None = None, *, max_age_hours: int = 24) -> int:
    """Remove leftover scan/fix workspaces older than `max_age_hours`.

    Working copies are intentionally kept after a run so a fix can be re-applied
    without cloning again, but they are disposable and must not accumulate.
    """
    settings = get_settings()
    directory = root or settings.workspaces_dir
    if not directory.is_dir():
        return 0

    cutoff = time.time() - (max_age_hours * 3600)
    removed = 0
    for entry in directory.iterdir():
        if not entry.is_dir():
            continue
        if not (entry.name.startswith("scan-") or entry.name.startswith("fix-")):
            continue
        try:
            if entry.stat().st_mtime >= cutoff:
                continue
        except OSError:
            continue
        shutil.rmtree(entry, ignore_errors=True)
        removed += 1

    if removed:
        logger.info("Pruned %d stale workspace(s)", removed)
    return removed
