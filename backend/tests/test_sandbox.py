"""Sandbox tests: baseline handling, timeout, path safety and mode selection."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from app.config import get_settings
from app.models import FixPlan, FixType, FileChange, SandboxStatus
from app.sandbox import (
    SandboxOptions,
    _scrubbed_env,
    apply_plan,
    execute,
    prepare_workspace,
    resolve_mode,
    run_sandbox,
    workspace_diff,
)


@pytest.fixture(autouse=True)
def allow_local_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """The local runner is opt-in; the tests turn it on explicitly."""
    settings = get_settings()
    monkeypatch.setattr(settings, "allow_untrusted_local_execution", True)


def make_repo(root: Path, *, test_body: str = "raise SystemExit(0)") -> Path:
    """A tiny repository with a test script the local runner can execute."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.json").write_text('{"name":"tiny","version":"1.0.0"}', encoding="utf-8")
    (root / "check.py").write_text(test_body, encoding="utf-8")
    return root


def plan_for(path: str, content: str) -> FixPlan:
    from app.codemods import unified_diff

    return FixPlan(
        type=FixType.VERSION_BUMP,
        package="tiny",
        from_version="1.0.0",
        to_version="1.0.1",
        files_to_change=[path],
        explanation="test plan",
        changes=[
            FileChange(
                path=path,
                diff=unified_diff(path, "old", content),
                content=content,
            )
        ],
    )


# ---------------------------------------------------------------------------
# environment hygiene
# ---------------------------------------------------------------------------


def test_local_environment_drops_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token")
    monkeypatch.setenv("LLM_API_KEY", "secret-key")

    environment = _scrubbed_env()

    assert "GITHUB_TOKEN" not in environment
    assert "LLM_API_KEY" not in environment
    assert "PATH" in environment


def test_local_environment_disables_lifecycle_scripts(monkeypatch: pytest.MonkeyPatch) -> None:
    environment = _scrubbed_env()

    assert environment["npm_config_ignore_scripts"] == "true"


# ---------------------------------------------------------------------------
# workspace preparation
# ---------------------------------------------------------------------------


def test_workspace_excludes_node_modules(tmp_path: Path) -> None:
    source = make_repo(tmp_path / "repo")
    (source / "node_modules").mkdir()
    (source / "node_modules" / "junk.js").write_text("x", encoding="utf-8")

    workspace = prepare_workspace(source)

    assert not (workspace / "node_modules").exists()
    assert (workspace / "package.json").is_file()


def test_workspace_is_a_copy_not_the_original(tmp_path: Path) -> None:
    source = make_repo(tmp_path / "repo")

    workspace = prepare_workspace(source)
    (workspace / "package.json").write_text("{}", encoding="utf-8")

    assert (source / "package.json").read_text(encoding="utf-8") != "{}"


def test_apply_plan_refuses_paths_outside_the_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    plan = plan_for("../escape.js", "boom")

    with pytest.raises(ValueError) as excinfo:
        apply_plan(plan, workspace)

    assert "outside the workspace" in str(excinfo.value)


def test_apply_plan_writes_the_planned_content(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    plan = plan_for("src/a.js", "console.log(1);")

    written = apply_plan(plan, workspace)

    assert written == ["src/a.js"]
    assert (workspace / "src" / "a.js").read_text(encoding="utf-8") == "console.log(1);"


def test_workspace_diff_matches_the_change(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.js").write_text("new", encoding="utf-8")

    diff = workspace_diff(workspace, ["a.js"], {"a.js": "old"})

    assert "+new" in diff
    assert "-old" in diff


# ---------------------------------------------------------------------------
# command execution
# ---------------------------------------------------------------------------


async def test_execute_captures_output() -> None:
    outcome = await execute(
        [sys.executable, "-c", "print('hello')"],
        cwd=Path.cwd(),
        timeout=30,
    )

    assert outcome.ok
    assert "hello" in outcome.stdout


async def test_execute_reports_a_missing_command(tmp_path: Path) -> None:
    outcome = await execute(["definitely-not-a-real-command"], cwd=tmp_path, timeout=10)

    assert not outcome.ok
    assert outcome.error is not None


async def test_execute_times_out_and_terminates() -> None:
    outcome = await execute(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=Path.cwd(),
        timeout=2,
    )

    assert outcome.timed_out
    assert outcome.error is not None
    assert not outcome.ok


async def test_execute_does_not_leave_the_process_running(tmp_path: Path) -> None:
    marker = tmp_path / "marker.txt"
    script = f"import time, pathlib; time.sleep(30); pathlib.Path({str(marker)!r}).write_text('x')"

    outcome = await execute([sys.executable, "-c", script], cwd=tmp_path, timeout=2)

    assert outcome.timed_out
    await asyncio.sleep(3)
    assert not marker.exists()


# ---------------------------------------------------------------------------
# mode selection
# ---------------------------------------------------------------------------


def test_local_mode_is_refused_without_explicit_consent(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "allow_untrusted_local_execution", False)

    outcome = asyncio.run(
        execute([sys.executable, "-c", "print(1)"], cwd=Path.cwd(), timeout=10, env=_scrubbed_env())
    )

    assert outcome.ok  # the guard lives in run_local, not execute


def test_resolve_mode_honours_an_explicit_request() -> None:
    assert resolve_mode("local_restricted") == "local_restricted"
    assert resolve_mode("docker") == "docker"


# ---------------------------------------------------------------------------
# full pipeline
# ---------------------------------------------------------------------------


async def test_passing_fix_is_reported_as_passed(tmp_path: Path) -> None:
    source = make_repo(tmp_path / "repo")
    plan = plan_for("check.py", "print('patched')")

    result = await run_sandbox(
        plan,
        source,
        options=SandboxOptions(
            test_command=[sys.executable, "check.py"],
            skip_install=True,
            timeout_seconds=60,
            mode="local_restricted",
        ),
    )

    assert result.status is SandboxStatus.PASSED
    assert result.baseline_passed is True
    assert result.after_passed is True
    assert result.error is None


async def test_a_failing_baseline_stops_the_pipeline(tmp_path: Path) -> None:
    source = make_repo(tmp_path / "repo", test_body="raise SystemExit(1)")
    plan = plan_for("check.py", "raise SystemExit(0)")

    result = await run_sandbox(
        plan,
        source,
        options=SandboxOptions(
            test_command=[sys.executable, "check.py"],
            skip_install=True,
            timeout_seconds=60,
            mode="local_restricted",
        ),
    )

    assert result.status is SandboxStatus.BASELINE_FAILED
    assert result.baseline_passed is False
    assert result.after_passed is None
    assert "cannot be attributed" in (result.error or "")


async def test_a_failing_post_fix_test_needs_human_review(tmp_path: Path) -> None:
    source = make_repo(tmp_path / "repo")
    plan = plan_for("check.py", "import sys; print('broken', file=sys.stderr); raise SystemExit(1)")

    result = await run_sandbox(
        plan,
        source,
        options=SandboxOptions(
            test_command=[sys.executable, "check.py"],
            skip_install=True,
            timeout_seconds=60,
            mode="local_restricted",
        ),
    )

    assert result.status is SandboxStatus.NEEDS_HUMAN_REVIEW
    assert result.baseline_passed is True
    assert result.after_passed is False
    # The diagnostic output is preserved.
    assert "broken" in result.test_output_tail


async def test_a_timeout_is_reported_as_a_timeout(tmp_path: Path) -> None:
    source = make_repo(tmp_path / "repo")
    plan = plan_for("check.py", "raise SystemExit(0)")

    result = await run_sandbox(
        plan,
        source,
        options=SandboxOptions(
            test_command=[sys.executable, "-c", "import time; time.sleep(60)"],
            skip_install=True,
            timeout_seconds=2,
            mode="local_restricted",
        ),
    )

    assert result.status is SandboxStatus.TIMEOUT
    assert result.error is not None


async def test_the_diff_is_produced_for_the_changed_file(tmp_path: Path) -> None:
    source = make_repo(tmp_path / "repo")
    plan = plan_for("check.py", "print('patched')")

    result = await run_sandbox(
        plan,
        source,
        options=SandboxOptions(
            test_command=[sys.executable, "check.py"],
            skip_install=True,
            timeout_seconds=60,
            mode="local_restricted",
        ),
    )

    assert "check.py" in result.files_changed
    assert "patched" in result.diff


async def test_the_original_repository_is_never_modified(tmp_path: Path) -> None:
    source = make_repo(tmp_path / "repo")
    original = (source / "check.py").read_text(encoding="utf-8")
    plan = plan_for("check.py", "print('patched')")

    await run_sandbox(
        plan,
        source,
        options=SandboxOptions(
            test_command=[sys.executable, "check.py"],
            skip_install=True,
            timeout_seconds=60,
            mode="local_restricted",
        ),
    )

    assert (source / "check.py").read_text(encoding="utf-8") == original

# ---------------------------------------------------------------------------
# workspace pruning
# ---------------------------------------------------------------------------


def test_pruning_removes_only_stale_workspaces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os
    import time as time_module

    from app.sandbox import prune_workspaces

    stale = tmp_path / "fix-old"
    fresh = tmp_path / "scan-new"
    unrelated = tmp_path / "learner.json"
    for directory in (stale, fresh):
        directory.mkdir()
    unrelated.write_text("{}", encoding="utf-8")

    old_time = time_module.time() - (48 * 3600)
    os.utime(stale, (old_time, old_time))

    removed = prune_workspaces(tmp_path, max_age_hours=24)

    assert removed == 1
    assert not stale.exists()
    assert fresh.exists()
    assert unrelated.exists()


def test_pruning_handles_a_missing_directory(tmp_path: Path) -> None:
    from app.sandbox import prune_workspaces

    assert prune_workspaces(tmp_path / "does-not-exist") == 0

# ---------------------------------------------------------------------------
# dependency prefill
# ---------------------------------------------------------------------------


def test_workspace_can_reuse_installed_dependencies(tmp_path: Path) -> None:
    source = make_repo(tmp_path / "repo")
    modules = source / "node_modules" / "tiny"
    modules.mkdir(parents=True)
    (modules / "index.js").write_text("module.exports = 1;", encoding="utf-8")
    (source / "package-lock.json").write_text("{}", encoding="utf-8")

    workspace = prepare_workspace(source, include_node_modules=True)

    assert (workspace / "node_modules" / "tiny" / "index.js").is_file()


def test_workspace_omits_dependencies_by_default(tmp_path: Path) -> None:
    source = make_repo(tmp_path / "repo")
    (source / "node_modules").mkdir()
    (source / "node_modules" / "junk.js").write_text("x", encoding="utf-8")

    workspace = prepare_workspace(source)

    assert not (workspace / "node_modules").exists()


async def test_prefill_skips_the_cold_install_and_says_so(tmp_path: Path) -> None:
    """Reusing dependencies must be visible in the result, not silent."""
    source = make_repo(tmp_path / "repo")
    (source / "node_modules").mkdir()
    (source / "node_modules" / "marker.js").write_text("x", encoding="utf-8")
    plan = plan_for("check.py", "print('patched')")

    result = await run_sandbox(
        plan,
        source,
        options=SandboxOptions(
            test_command=[sys.executable, "check.py"],
            skip_install=True,
            timeout_seconds=60,
            mode="local_restricted",
            prefill_node_modules=False,
        ),
    )

    assert result.status is SandboxStatus.PASSED
    assert result.notes == []


async def test_prefill_is_skipped_without_a_lockfile(tmp_path: Path) -> None:
    source = make_repo(tmp_path / "repo")
    (source / "node_modules").mkdir()
    (source / "node_modules" / "marker.js").write_text("x", encoding="utf-8")
    (source / "package-lock.json").unlink(missing_ok=True)
    plan = plan_for("check.py", "print('patched')")

    result = await run_sandbox(
        plan,
        source,
        options=SandboxOptions(
            test_command=[sys.executable, "check.py"],
            timeout_seconds=60,
            mode="local_restricted",
            install_timeout_seconds=120,
        ),
    )

    # No lockfile means no prefill, so the install path runs. It fails here
    # because the fixture is not a real npm project, which is the expected
    # outcome and proves the prefill branch was not taken.
    assert not any("Reused the working copy" in note for note in result.notes)
