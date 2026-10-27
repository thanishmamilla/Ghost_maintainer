"""Source reachability analysis.

Scans a repository's source files for references to each declared dependency and
reports where they were found. The result is deliberately expressed as an
observation, never as proof:

* ``observed``      — an import or usage of the package was found in the source.
* ``not_observed``  — no reference was found in the files that were scanned.
* ``unknown``       — the scan could not establish a result.

A package that was not observed is *not* proven unreachable. It may be reached
transitively, loaded dynamically, aliased, or imported from a file type this
scanner does not parse. Every result carries that limitation.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from .models import Dependency, ReachabilityClass, ReachabilityResult, UsageMatch, Vulnerability

logger = logging.getLogger("ghost_maintainer.reachability")

SUPPORTED_EXTENSIONS = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}

EXCLUDED_DIRECTORIES = {
    "node_modules",
    ".git",
    ".hg",
    ".svn",
    "dist",
    "build",
    "out",
    "coverage",
    ".next",
    ".nuxt",
    ".svelte-kit",
    ".turbo",
    ".cache",
    ".parcel-cache",
    "vendor",
    "__pycache__",
    ".venv",
    "venv",
    "target",
    "bower_components",
    ".idea",
    ".vscode",
    "storybook-static",
}

EXCLUDED_FILENAMES = {
    ".min.js",
    ".bundle.js",
    ".chunk.js",
}

# Directories that are build output even though they are not always named
# predictably, matched against the path relative to the repository root.
_GENERATED_MARKERS = ("generated", "__generated__", ".generated")

# `import ... from "pkg"`, `import "pkg"`, `export ... from "pkg"`
_ESM_RE = re.compile(
    r"""(?P<kind>import|export)\b[^;\n]*?\bfrom\s*["'](?P<specifier>[^"']+)["']""",
    re.MULTILINE,
)
_SIDE_EFFECT_RE = re.compile(
    r"""^\s*(?P<kind>import)\s*["'](?P<specifier>[^"']+)["']""",
    re.MULTILINE,
)
_REQUIRE_RE = re.compile(
    r"""(?P<kind>require)\s*\(\s*["'](?P<specifier>[^"']+)["']\s*\)""",
)
_DYNAMIC_RE = re.compile(
    r"""(?P<kind>import)\s*\(\s*["'](?P<specifier>[^"']+)["']\s*\)""",
)

# Named members pulled out of an ESM import clause.
_NAMED_IMPORT_RE = re.compile(r"\{(?P<members>[^}]*)\}")
# Destructured members from a require call.
_DESTRUCTURED_REQUIRE_RE = re.compile(r"\{(?P<members>[^}]*)\}")
# A default binding assigned from a require call, e.g. `const _ = require(...)`.
_DEFAULT_BINDING_RE = re.compile(r"(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*$")
# A default binding in an ESM import, e.g. `import moment from "moment"`.
_ESM_DEFAULT_RE = re.compile(r"\bimport\s+(?P<name>[A-Za-z_$][\w$]*)\s*(?:,|from\b)")

_LIMITATION = (
    "Source scanning is a heuristic. A package with no observed import may still be reached "
    "transitively, loaded dynamically, aliased, or used from a file type this scanner does not parse."
)


@dataclass
class SourceFile:
    path: Path
    relative_path: str
    text: str


@dataclass
class ScanOutcome:
    matches: list[UsageMatch] = field(default_factory=list)
    scanned_files: int = 0
    scanned_directories: int = 0


def package_root(specifier: str) -> str | None:
    """Reduce an import specifier to its package name.

    ``lodash/fp`` belongs to ``lodash``; ``@scope/pkg/sub`` belongs to ``@scope/pkg``.
    Relative and absolute paths are not packages.
    """
    if not specifier:
        return None
    if specifier.startswith("."):
        return None
    if specifier.startswith("/") or specifier.startswith("~"):
        return None
    if specifier.startswith("node:"):
        return None

    parts = specifier.split("/")
    if specifier.startswith("@"):
        if len(parts) < 2:
            return None
        return f"{parts[0]}/{parts[1]}"
    return parts[0]


def _is_excluded(relative_path: Path) -> bool:
    parts = relative_path.parts
    for part in parts[:-1]:
        if part in EXCLUDED_DIRECTORIES:
            return True
        if part.lower() in EXCLUDED_DIRECTORIES:
            return True
        if any(marker in part.lower() for marker in _GENERATED_MARKERS):
            return True
    name = parts[-1].lower()
    for suffix in EXCLUDED_FILENAMES:
        if name.endswith(suffix):
            return True
    return False


def collect_source_files(root: Path) -> list[SourceFile]:
    """Collect the source files a reachability scan should read."""
    files: list[SourceFile] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        try:
            relative = path.relative_to(root)
        except ValueError:  # pragma: no cover - defensive
            continue
        if _is_excluded(relative):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            logger.warning("Could not read %s", path)
            continue
        files.append(SourceFile(path=path, relative_path=relative.as_posix(), text=text))
    return files


def _clean_snippet(text: str, line: str) -> str:
    snippet = line.strip()
    if len(snippet) > 200:
        snippet = snippet[:197] + "..."
    return snippet


def _extract_named_members(clause: str) -> list[str]:
    members: list[str] = []
    for part in clause.split(","):
        name = part.strip()
        if not name:
            continue
        name = name.split(" as ")[0].strip()
        name = name.replace("type ", "").strip()
        if name and name.isidentifier():
            members.append(name)
    return members


def find_imports(file: SourceFile) -> list[UsageMatch]:
    """Find every package reference in one source file."""
    matches: list[UsageMatch] = []
    seen: set[tuple[int, str]] = set()

    def add(line_number: int, column: int, kind: str, specifier: str, members: list[str]) -> None:
        key = (line_number, specifier)
        if key in seen:
            return
        seen.add(key)
        lines = file.text.splitlines()
        snippet = _clean_snippet(file.text, lines[line_number - 1]) if 0 < line_number <= len(lines) else ""
        matches.append(
            UsageMatch(
                file=file.relative_path,
                line=line_number,
                column=column,
                snippet=snippet,
                kind=kind,  # type: ignore[arg-type]
                specifier=specifier,
                members=members,
            )
        )

    for match in _ESM_RE.finditer(file.text):
        specifier = match.group("specifier")
        if package_root(specifier) is None:
            continue
        line_number = file.text.count("\n", 0, match.start()) + 1
        clause_match = _NAMED_IMPORT_RE.search(match.group(0))
        members = _extract_named_members(clause_match.group("members")) if clause_match else []
        default_match = _ESM_DEFAULT_RE.search(match.group(0))
        if default_match:
            members.append(default_match.group("name"))
        kind = "import" if match.group("kind") == "import" else "side_effect"
        add(line_number, match.start() + 1, kind, specifier, members)

    for match in _SIDE_EFFECT_RE.finditer(file.text):
        specifier = match.group("specifier")
        if package_root(specifier) is None:
            continue
        line_number = file.text.count("\n", 0, match.start()) + 1
        add(line_number, match.start() + 1, "side_effect", specifier, [])

    for match in _REQUIRE_RE.finditer(file.text):
        specifier = match.group("specifier")
        if package_root(specifier) is None:
            continue
        line_number = file.text.count("\n", 0, match.start()) + 1
        # Look back a little for a destructuring or a default binding.
        prefix = file.text[max(0, match.start() - 200) : match.start()]
        members: list[str] = []
        destructured = _DESTRUCTURED_REQUIRE_RE.search(prefix)
        if destructured:
            members = _extract_named_members(destructured.group("members"))
        default_binding = _DEFAULT_BINDING_RE.search(prefix)
        if default_binding:
            members.append(default_binding.group("name"))
        add(line_number, match.start() + 1, "require", specifier, members)

    for match in _DYNAMIC_RE.finditer(file.text):
        specifier = match.group("specifier")
        if package_root(specifier) is None:
            continue
        line_number = file.text.count("\n", 0, match.start()) + 1
        add(line_number, match.start() + 1, "dynamic_import", specifier, [])

    return matches


def scan_source(root: Path) -> ScanOutcome:
    """Scan a repository for package references."""
    outcome = ScanOutcome()
    for source_file in collect_source_files(root):
        outcome.scanned_files += 1
        outcome.matches.extend(find_imports(source_file))
    return outcome


def _explanation(
    package: str,
    matches: list[UsageMatch],
    scanned_files: int,
) -> str:
    if matches:
        files = sorted({match.file for match in matches})
        shown = ", ".join(files[:3])
        extra = f" and {len(files) - 3} more file(s)" if len(files) > 3 else ""
        kinds = sorted({match.kind.replace("_", " ") for match in matches})
        return (
            f"`{package}` was observed in {len(matches)} reference(s) across {len(files)} file(s) "
            f"({shown}{extra}) using {', '.join(kinds)}."
        )
    if scanned_files == 0:
        return (
            f"No supported source files were found, so the usage of `{package}` could not be established."
        )
    return (
        f"No import or usage of `{package}` was found in the {scanned_files} source file(s) that were scanned. "
        "This does not prove the package is unused: it may be reached transitively, loaded dynamically, "
        "aliased, or imported from a file type this scanner does not parse."
    )


def _vulnerability_reachability(
    vulnerabilities: list[Vulnerability],
    used_members: list[str],
) -> str:
    """Compare advisory-affected functions against the members actually used."""
    affected_functions: set[str] = set()
    for vulnerability in vulnerabilities:
        affected_functions.update(vulnerability.affected_functions)

    if not affected_functions:
        return (
            "The advisories do not name specific affected functions, so function-level reachability "
            "is unknown for this package."
        )

    if not used_members:
        return (
            "The advisories name affected functions, but no member usage could be identified in the "
            "source, so function-level reachability is unknown."
        )

    overlap = sorted(affected_functions.intersection(used_members))
    if overlap:
        return (
            f"Member usage overlaps with functions named in the advisories: {', '.join(overlap)}. "
            "This is a heuristic match, not a complete static analysis."
        )

    return (
        f"The advisories name {len(affected_functions)} affected function(s), and none of them match the "
        f"identified usage ({', '.join(sorted(used_members)) or 'none'}). This is a heuristic match, "
        "not a complete static analysis."
    )


def analyse(
    root: Path,
    dependencies: list[Dependency],
    vulnerabilities_by_package: dict[str, list[Vulnerability]] | None = None,
) -> dict[str, ReachabilityResult]:
    """Determine, for each dependency, whether the source appears to use it."""
    vulnerabilities_by_package = vulnerabilities_by_package or {}
    outcome = scan_source(root)

    by_package: dict[str, list[UsageMatch]] = {}
    for match in outcome.matches:
        root_name = package_root(match.specifier)
        if root_name:
            by_package.setdefault(root_name, []).append(match)

    results: dict[str, ReachabilityResult] = {}
    for dependency in dependencies:
        matches = by_package.get(dependency.name, [])
        used_members = sorted({member for match in matches for member in match.members})

        if outcome.scanned_files == 0:
            classification = ReachabilityClass.UNKNOWN
        elif matches:
            classification = ReachabilityClass.OBSERVED
        else:
            classification = ReachabilityClass.NOT_OBSERVED

        explanation = _explanation(dependency.name, matches, outcome.scanned_files)
        vulnerabilities = vulnerabilities_by_package.get(dependency.name, [])
        if vulnerabilities:
            explanation = f"{explanation} {_vulnerability_reachability(vulnerabilities, used_members)}"

        results[dependency.name] = ReachabilityResult(
            classification=classification,
            explanation=explanation,
            matches=matches[:50],
            used_members=used_members,
            scanned_files=outcome.scanned_files,
            limitation=_LIMITATION,
        )

    return results
