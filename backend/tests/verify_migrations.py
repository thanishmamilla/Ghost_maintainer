"""Verify a codemod against the real demo repository.

Copies the demo into a temporary directory, applies the migration, installs the
replacement dependency and runs the repository's own test suite. This is the
check that a migration actually works, not just that it produces a diff.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.codemods import apply_codemod, collect_source_files  # noqa: E402
from app.fixer import _update_manifest  # noqa: E402

DEMO = Path(__file__).resolve().parent.parent.parent / "demo-rotten-app"


def _npm_command() -> str:
    """Resolve the npm launcher, which is a batch file on Windows."""
    for candidate in ("npm.cmd", "npm"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return "npm"


def run(command: list[str], cwd: Path) -> subprocess.CompletedProcess:
    if command and command[0] == "npm":
        command = [_npm_command(), *command[1:]]
    return subprocess.run(  # noqa: S603
        command, cwd=str(cwd), capture_output=True, text=True, timeout=600, shell=False
    )


def verify(package: str, replacement: str, replacement_version: str) -> bool:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "app"
        shutil.copytree(DEMO, root, ignore=shutil.ignore_patterns("node_modules"))

        sources = collect_source_files(root, package)
        result = apply_codemod(package, sources, replacement=replacement)
        if not result.supported:
            print(f"  REFUSED: {result.reason}")
            return False

        for edit in result.edits:
            target = root / edit.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(edit.updated, encoding="utf-8")

        manifest_path = root / "package.json"
        manifest = manifest_path.read_text(encoding="utf-8")
        manifest_path.write_text(
            _update_manifest(
                manifest,
                remove=package,
                add=(replacement, f"^{replacement_version}"),
            ),
            encoding="utf-8",
        )

        print(f"  changed files: {', '.join(result.changed_files)}")
        install = run(["npm", "install", "--no-fund", "--no-audit"], root)
        if install.returncode != 0:
            print(f"  npm install FAILED:\n{install.stdout[-1500:]}\n{install.stderr[-800:]}")
            return False

        test = run(["npm", "test", "--silent"], root)
        print(f"  npm test exit code: {test.returncode}")
        if test.returncode != 0:
            print(f"  STDOUT:\n{test.stdout[-2500:]}")
            print(f"  STDERR:\n{test.stderr[-1500:]}")
            return False

        summary = [line for line in test.stdout.splitlines() if "Tests:" in line or "Test Suites:" in line]
        for line in summary:
            print(f"  {line.strip()}")
        return True


def main() -> None:
    print("== moment -> dayjs ==")
    moment_ok = verify("moment", "dayjs", "1.11.23")

    print()
    print("== request -> axios ==")
    request_ok = verify("request", "axios", "1.20.0")

    print()
    print(f"moment migration: {'PASS' if moment_ok else 'FAIL'}")
    print(f"request migration: {'PASS' if request_ok else 'FAIL'}")
    sys.exit(0 if (moment_ok and request_ok) else 1)


if __name__ == "__main__":
    main()
