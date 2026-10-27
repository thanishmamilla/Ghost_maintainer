"""Fix planning.

Two kinds of fix are supported:

**Safe version bump.** When an advisory names a fixed version that is still in the
same major line as the installed version, the dependency is bumped to the lowest
version that resolves *every* advisory affecting it. Cross-major upgrades are
never performed automatically: they are reported so a human can decide.

**Package replacement.** A curated mapping from ``swaps.json`` is applied with a
deterministic codemod. When the code uses the package in a way the codemod does
not recognise, the plan is refused and marked for human review rather than
producing broken code.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .codemods import CodemodResult, FileEdit, apply_codemod, collect_source_files, unified_diff
from .models import (
    DependencyReport,
    FixOption,
    FixPlan,
    FixType,
    FileChange,
)
from .semver_utils import Version, latest_stable, same_major

logger = logging.getLogger("ghost_maintainer.fixer")

SWAPS_PATH = Path(__file__).resolve().parent / "swaps.json"


def load_swaps() -> dict[str, Any]:
    """Load the curated replacement mappings."""
    try:
        payload = json.loads(SWAPS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.error("Could not read swaps.json: %s", exc)
        return {"swaps": {}}
    return payload


def swap_for(package: str) -> dict[str, Any] | None:
    """The curated mapping for a package, if one exists."""
    swaps = load_swaps().get("swaps") or {}
    entry = swaps.get(package)
    return entry if isinstance(entry, dict) else None


# ---------------------------------------------------------------------------
# safe version bumps
# ---------------------------------------------------------------------------


@dataclass
class BumpAnalysis:
    """The outcome of looking for a safe bump."""

    target: str | None = None
    fixed_candidates: list[str] = field(default_factory=list)
    cross_major_only: bool = False
    notes: list[str] = field(default_factory=list)


def _field_default() -> list[str]:  # pragma: no cover - dataclass helper
    return []


def analyse_bump(report: DependencyReport) -> BumpAnalysis:
    """Find the lowest same-major version that resolves every known advisory."""
    analysis = BumpAnalysis()

    if not report.version:
        analysis.notes.append(
            "The installed version is unknown, so a safe upgrade target cannot be established."
        )
        return analysis

    installed = Version.try_parse(report.version)
    if installed is None:
        analysis.notes.append(f"The installed version {report.version!r} is not a semantic version.")
        return analysis

    candidates: list[str] = []
    for vulnerability in report.vulnerabilities:
        for fixed in vulnerability.fixed_versions:
            if fixed not in candidates:
                candidates.append(fixed)

    if not candidates:
        analysis.notes.append("No advisory states a fixed version for this package.")
        return analysis

    analysis.fixed_candidates = sorted(candidates, key=lambda value: Version.parse(value))

    usable = [
        candidate
        for candidate in candidates
        if Version.parse(candidate) > installed and same_major(candidate, report.version)
    ]
    if not usable:
        analysis.cross_major_only = True
        analysis.notes.append(
            "Every fixed version upstream is in a different major line, so an automatic upgrade would "
            "be a breaking change. It is left for a human to decide."
        )
        return analysis

    # The lowest same-major version that is at or above every advisory's fix marker.
    required = max(
        (candidate for candidate in candidates if same_major(candidate, report.version)),
        key=lambda value: Version.parse(value),
        default=None,
    )
    if required is None:
        analysis.notes.append("No same-major fixed version is available.")
        return analysis

    target = min(
        (candidate for candidate in usable if Version.parse(candidate) >= Version.parse(required)),
        key=lambda value: Version.parse(value),
        default=None,
    )
    if target is None:
        analysis.notes.append("No same-major version resolves every advisory.")
        return analysis

    analysis.target = target
    if report.latest_version and Version.try_parse(report.latest_version):
        if Version.parse(target) < Version.parse(report.latest_version):
            analysis.notes.append(
                f"{report.latest_version} is the latest release, but {target} is the lowest version that "
                "resolves every advisory, so the smaller change is proposed."
            )
    return analysis


# ---------------------------------------------------------------------------
# manifest editing
# ---------------------------------------------------------------------------


def _read_manifest(root: Path) -> dict:
    path = root / "package.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Could not read package.json: {exc}") from exc


def _update_manifest(
    original: str,
    *,
    remove: str | None = None,
    add: tuple[str, str] | None = None,
) -> str:
    """Update a package manifest, preserving the original formatting where possible."""
    manifest = json.loads(original)
    changed = False

    for section in ("dependencies", "devDependencies"):
        block = manifest.get(section)
        if not isinstance(block, dict):
            continue
        if remove and remove in block:
            del block[remove]
            changed = True
        if add and add[0] not in block:
            block[add[0]] = add[1]
            changed = True

    if not changed:
        return original

    # Preserve the original indentation and trailing newline.
    indent = 2
    for line in original.splitlines():
        stripped = line.lstrip()
        if stripped and len(line) - len(stripped) > indent:
            indent = len(line) - len(stripped)
            break
    updated = json.dumps(manifest, indent=indent, ensure_ascii=False)
    if original.endswith("\n"):
        updated += "\n"
    return updated


# ---------------------------------------------------------------------------
# plans
# ---------------------------------------------------------------------------


def _file_change(edit: FileEdit) -> FileChange:
    added = removed = 0
    for line in unified_diff(edit.path, edit.original, edit.updated).splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return FileChange(
        path=edit.path,
        diff=unified_diff(edit.path, edit.original, edit.updated),
        content=edit.updated,
        added_lines=added,
        removed_lines=removed,
    )


def build_bump_plan(report: DependencyReport, root: Path) -> FixPlan:
    """Plan a same-major version bump for a dependency."""
    analysis = analyse_bump(report)

    if analysis.target is None:
        return FixPlan(
            type=FixType.NONE,
            package=report.name,
            from_version=report.version,
            explanation=analysis.notes[0] if analysis.notes else "No safe upgrade is available.",
            requires_human_review=True,
            evidence=analysis.notes,
        )

    manifest_path = root / "package.json"
    original_manifest = manifest_path.read_text(encoding="utf-8")
    updated_manifest = _update_manifest(original_manifest, add=(report.name, f"^{analysis.target}"))

    edits = [FileEdit(path="package.json", original=original_manifest, updated=updated_manifest)]

    evidence = [
        f"The installed version {report.version} is affected by "
        f"{len(report.vulnerabilities)} advisory/advisories.",
    ]
    for vulnerability in report.vulnerabilities:
        fixed = ", ".join(vulnerability.fixed_versions) or "no fixed version stated"
        evidence.append(
            f"{vulnerability.id} ({vulnerability.severity.value}) is fixed in {fixed}."
        )
    evidence.append(
        f"{analysis.target} is the lowest same-major version that resolves every advisory affecting "
        f"{report.version}."
    )

    return FixPlan(
        type=FixType.VERSION_BUMP,
        package=report.name,
        from_version=report.version,
        to_version=analysis.target,
        files_to_change=["package.json"],
        explanation=(
            f"Upgrade `{report.name}` from {report.version} to {analysis.target}. The upgrade stays "
            "within the same major version, so no breaking API change is expected."
        ),
        migration_notes=analysis.notes,
        evidence=evidence,
        requires_human_review=False,
        changes=[_file_change(edit) for edit in edits],
    )


def build_replacement_plan(report: DependencyReport, root: Path) -> FixPlan:
    """Plan a package replacement using a curated mapping and a deterministic codemod."""
    mapping = swap_for(report.name)
    if mapping is None:
        return FixPlan(
            type=FixType.NONE,
            package=report.name,
            from_version=report.version,
            explanation=f"No curated replacement is registered for `{report.name}`.",
            requires_human_review=True,
        )

    replacement = str(mapping.get("replacement") or "")
    replacement_version = mapping.get("replacement_version")

    source_files = collect_source_files(root, report.name)
    codemod: CodemodResult = apply_codemod(report.name, source_files, replacement=replacement or None)

    if not codemod.supported:
        return FixPlan(
            type=FixType.PACKAGE_REPLACEMENT,
            package=report.name,
            from_version=report.version,
            replacement=replacement,
            files_to_change=[],
            explanation=(
                f"`{report.name}` is being replaced with `{replacement}`, but the usage could not be "
                "migrated automatically."
            ),
            migration_notes=list(mapping.get("notes") or []) + [codemod.reason],
            evidence=[
                f"Curated mapping `{report.name}` -> `{replacement}`.",
                f"Unsupported usage: {codemod.reason}",
            ],
            requires_human_review=True,
            changes=[],
        )

    edits = list(codemod.edits)

    manifest_path = root / "package.json"
    original_manifest = manifest_path.read_text(encoding="utf-8")
    version_range = f"^{replacement_version}" if replacement_version else "latest"
    updated_manifest = _update_manifest(
        original_manifest,
        remove=report.name,
        add=(replacement, version_range),
    )
    if updated_manifest != original_manifest:
        edits.insert(0, FileEdit(path="package.json", original=original_manifest, updated=updated_manifest))

    supported_patterns = list(mapping.get("supported_patterns") or [])
    unsupported_patterns = list(mapping.get("unsupported_patterns") or [])
    notes = list(mapping.get("notes") or []) + codemod.warnings

    summary = str(mapping.get("summary") or "").strip()
    explanation = summary or f"Replace `{report.name}` with `{replacement}`."

    return FixPlan(
        type=FixType.PACKAGE_REPLACEMENT,
        package=report.name,
        from_version=report.version,
        replacement=replacement,
        to_version=str(replacement_version) if replacement_version else None,
        files_to_change=[edit.path for edit in edits],
        explanation=explanation,
        migration_notes=notes,
        evidence=[
            f"Curated mapping `{report.name}` -> `{replacement}`: {mapping.get('why', '')}",
            f"Supported patterns: {'; '.join(supported_patterns) if supported_patterns else 'none'}",
            *(
                [f"Unsupported patterns that are not rewritten: {'; '.join(unsupported_patterns)}"]
                if unsupported_patterns
                else []
            ),
            f"{len(edits)} file(s) changed by the deterministic codemod.",
        ],
        requires_human_review=False,
        changes=[_file_change(edit) for edit in edits],
    )


def offer_fix_options(report: DependencyReport, root: Path) -> list[FixOption]:
    """List the fixes that could be proposed for a dependency."""
    options: list[FixOption] = []

    analysis = analyse_bump(report)
    if analysis.target:
        options.append(
            FixOption(
                type=FixType.VERSION_BUMP,
                summary=(
                    f"Upgrade `{report.name}` from {report.version} to {analysis.target} within the same "
                    "major version."
                ),
                from_version=report.version,
                to_version=analysis.target,
                requires_human_review=False,
                migration_notes=analysis.notes,
            )
        )
    elif analysis.cross_major_only:
        options.append(
            FixOption(
                type=FixType.VERSION_BUMP,
                summary=(
                    f"Every upstream fix for `{report.name}` is in a different major version, so an "
                    "automatic upgrade would be a breaking change."
                ),
                from_version=report.version,
                to_version=analysis.fixed_candidates[-1] if analysis.fixed_candidates else None,
                requires_human_review=True,
                migration_notes=analysis.notes,
            )
        )

    mapping = swap_for(report.name)
    if mapping:
        replacement = str(mapping.get("replacement") or "")
        source_files = collect_source_files(root, report.name)
        codemod = apply_codemod(report.name, source_files, replacement=replacement or None)
        options.append(
            FixOption(
                type=FixType.PACKAGE_REPLACEMENT,
                summary=str(mapping.get("summary") or f"Replace `{report.name}` with `{replacement}`."),
                from_version=report.version,
                replacement=replacement,
                requires_human_review=not codemod.supported,
                migration_notes=(
                    list(mapping.get("notes") or [])
                    + ([codemod.reason] if not codemod.supported else [])
                ),
            )
        )

    return options


def plan_fix(
    report: DependencyReport,
    root: Path,
    fix_type: FixType,
) -> FixPlan:
    """Build the plan for a chosen fix type."""
    if fix_type is FixType.VERSION_BUMP:
        return build_bump_plan(report, root)
    if fix_type is FixType.PACKAGE_REPLACEMENT:
        return build_replacement_plan(report, root)
    return FixPlan(
        type=FixType.NONE,
        package=report.name,
        from_version=report.version,
        explanation="No fix type was requested.",
        requires_human_review=True,
    )
