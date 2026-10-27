"""Deterministic source-to-source migrations.

Each codemod works on a strict, declared pattern. When a file uses a package in
a way the codemod does not recognise, the migration is refused with an explicit
reason rather than producing broken code. The application never rewrites code it
does not understand.

Every codemod returns the files it changed, so the caller can produce a diff and
run the result through the test suite before anything is proposed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FileEdit:
    """One file's proposed change."""

    path: str
    original: str
    updated: str

    @property
    def changed(self) -> bool:
        return self.original != self.updated


@dataclass
class CodemodResult:
    """The outcome of attempting a migration."""

    edits: list[FileEdit] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    supported: bool = True
    reason: str = ""

    @property
    def changed_files(self) -> list[str]:
        return [edit.path for edit in result_files(self.edits) if edit.changed]


def result_files(edits: list[FileEdit]) -> list[FileEdit]:
    return edits


# ---------------------------------------------------------------------------
# lexical helpers
# ---------------------------------------------------------------------------


def mask_code(text: str) -> str:
    """Return the source with comments and string literals blanked out.

    Identifiers are only renamed in code, never inside a comment or a string,
    and a name that merely appears in prose never counts as a clash.
    """
    result = list(text)
    index = 0
    length = len(text)

    while index < length:
        char = text[index]
        if char == "/" and index + 1 < length and text[index + 1] == "/":
            end = text.find("\n", index)
            end = length if end == -1 else end
            for position in range(index, end):
                result[position] = " "
            index = end
        elif char == "/" and index + 1 < length and text[index + 1] == "*":
            end = text.find("*/", index + 2)
            end = length if end == -1 else end + 2
            for position in range(index, end):
                result[position] = " "
            index = end
        elif char in ('"', "'", "`"):
            quote = char
            end = index + 1
            while end < length:
                if text[end] == "\\":
                    end += 2
                    continue
                if text[end] == quote:
                    end += 1
                    break
                end += 1
            for position in range(index, min(end, length)):
                result[position] = " "
            index = end
        else:
            index += 1

    return "".join(result)


def rename_identifier(text: str, mask: str, old: str, new: str) -> str:
    """Rename a standalone identifier in code only."""
    pattern = re.compile(rf"(?<![\w.$]){re.escape(old)}\b")
    pieces: list[str] = []
    cursor = 0
    for match in pattern.finditer(mask):
        pieces.append(text[cursor : match.start()])
        pieces.append(new)
        cursor = match.end()
    pieces.append(text[cursor:])
    return "".join(pieces)


# ---------------------------------------------------------------------------
# import handling
# ---------------------------------------------------------------------------

_REQUIRE_RE = re.compile(
    r"""(?P<prefix>^[ \t]*)(?:const|let|var)\s+(?P<binding>[A-Za-z_$][\w$]*)\s*=\s*"""
    r"""require\(\s*["'](?P<package>[^"']+)["']\s*\)\s*;?""",
    re.MULTILINE,
)
_ESM_DEFAULT_RE = re.compile(
    r"""(?P<prefix>^[ \t]*)import\s+(?P<binding>[A-Za-z_$][\w$]*)\s+from\s+"""
    r"""["'](?P<package>[^"']+)["']\s*;?""",
    re.MULTILINE,
)
_ESM_NAMESPACE_RE = re.compile(
    r"""(?P<prefix>^[ \t]*)import\s+\*\s+as\s+(?P<binding>[A-Za-z_$][\w$]*)\s+from\s+"""
    r"""["'](?P<package>[^"']+)["']\s*;?""",
    re.MULTILINE,
)
_ESM_NAMED_RE = re.compile(
    r"""(?P<prefix>^[ \t]*)import\s+\{(?P<members>[^}]*)\}\s+from\s+"""
    r"""["'](?P<package>[^"']+)["']\s*;?""",
    re.MULTILINE,
)

_IMPORT_PATTERNS = (
    ("require", _REQUIRE_RE),
    ("esm_default", _ESM_DEFAULT_RE),
    ("esm_namespace", _ESM_NAMESPACE_RE),
    ("esm_named", _ESM_NAMED_RE),
)


@dataclass
class ImportInfo:
    """Where and how a package is imported."""

    kind: str
    match: re.Match[str]
    binding: str
    members: str = ""


def find_import(text: str, package: str, mask: str | None = None) -> ImportInfo | None:
    """Locate the import of `package`.

    The search runs over the original text, because the package name lives in a
    string literal. When a code mask is supplied, a match that falls inside a
    comment is rejected.
    """
    for kind, pattern in _IMPORT_PATTERNS:
        for match in pattern.finditer(text):
            if match.group("package") != package:
                continue
            if mask is not None and mask[match.start() : match.start() + 1].strip() == "":
                continue
            groups = match.groupdict()
            if kind == "esm_named":
                return ImportInfo(kind=kind, match=match, binding="", members=groups.get("members", ""))
            return ImportInfo(kind=kind, match=match, binding=groups.get("binding", ""))
    return None


def _render_import(info: ImportInfo, replacement: str) -> str:
    """Render the replacement import, preserving the original style."""
    prefix = info.match.group("prefix")
    if info.kind == "esm_named":
        return f'{prefix}import {{{info.members}}} from "{replacement}";'
    if info.kind in {"esm_default", "esm_namespace"}:
        return f'{prefix}import {info.binding} from "{replacement}";'
    return f'{prefix}const {info.binding} = require("{replacement}");'


def _uses_identifier(mask: str, name: str) -> bool:
    return re.search(rf"(?<![\w.$]){re.escape(name)}\b", mask) is not None


# ---------------------------------------------------------------------------
# diff helpers
# ---------------------------------------------------------------------------


def unified_diff(path: str, original: str, updated: str, context: int = 3) -> str:
    """Produce a unified diff for one file."""
    import difflib

    if original == updated:
        return ""
    diff = difflib.unified_diff(
        original.splitlines(keepends=True),
        updated.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        n=context,
    )
    return "".join(diff)


# ---------------------------------------------------------------------------
# moment -> dayjs
# ---------------------------------------------------------------------------

# moment APIs that dayjs does not provide with the same behaviour.
UNSUPPORTED_MOMENT_METHODS = [
    "locale",
    "calendar",
    "weekday",
    "week",
    "quarter",
    "isoWeek",
    "fromNow",
    "duration",
    "tz",
]


def moment_to_dayjs(files: dict[str, str], *, replacement: str = "dayjs") -> CodemodResult:
    """Migrate `moment` usage to `dayjs`.

    Supported: a default or namespace import of `moment` whose call sites use the
    formatting and arithmetic API. Refused: locale handling, calendar output and
    any other moment-specific API.
    """
    result = CodemodResult()
    target = "moment"

    for path, original in files.items():
        mask = mask_code(original)
        info = find_import(original, target, mask)
        if info is None:
            continue

        if info.kind == "esm_named":
            result.supported = False
            result.reason = (
                f"{path}: `{target}` is imported with named members. The replacement's exports "
                "cannot be assumed to match, so the import was left untouched."
            )
            return result

        if _uses_identifier(mask, replacement):
            result.supported = False
            result.reason = (
                f"{path}: the identifier `{replacement}` is already in use, so renaming "
                f"`{info.binding}` would be ambiguous."
            )
            return result

        for method in UNSUPPORTED_MOMENT_METHODS:
            if re.search(rf"\.{method}\s*\(", mask):
                result.supported = False
                result.reason = (
                    f"{path}: uses `.{method}()`, which `{replacement}` does not provide with the same "
                    "behaviour. This usage was not rewritten."
                )
                return result

        updated = original[: info.match.start()] + _render_import(info, replacement) + original[
            info.match.end() :
        ]
        updated = rename_identifier(updated, mask_code(updated), info.binding, replacement)

        result.edits.append(FileEdit(path=path, original=original, updated=updated))

    if not result.edits:
        result.supported = False
        result.reason = "No file imports `moment`, so there is nothing to migrate."
        return result

    result.warnings.append(
        "`dayjs` objects are immutable while `moment` objects are mutable. Chained calls are safe; "
        "code that stores a moment and mutates it later needs a human review."
    )
    return result


# ---------------------------------------------------------------------------
# request -> axios
# ---------------------------------------------------------------------------

AXIOS_ADAPTER = '''/**
 * `axios` adapter exposing the same callback-style `get(url, callback)` interface
 * the module expects, so no caller has to change.
 */
const httpClient = {
  get(url, callback) {
    axios
      .get(url)
      .then((response) => {
        const body =
          typeof response.data === "string" ? response.data : JSON.stringify(response.data);
        callback(null, { statusCode: response.status }, body);
      })
      .catch((error) => {
        // A response with an error status is passed through so the caller's own
        // status handling produces exactly the same message as before.
        if (error.response) {
          callback(null, { statusCode: error.response.status }, "");
          return;
        }
        callback(error);
      });
  },
};'''

# The `request` methods the adapter can stand in for.
SUPPORTED_REQUEST_METHODS = {"get"}


def request_to_axios(files: dict[str, str], *, replacement: str = "axios") -> CodemodResult:
    """Migrate `request` usage to `axios`.

    Supported: the `request` binding is used only as the default value of a
    client parameter, which is the shape this repository uses. Refused: direct
    `request.get(...)` calls and every other request method, because the callback
    API cannot be rewritten without changing behaviour.
    """
    result = CodemodResult()
    target = "request"

    for path, original in files.items():
        mask = mask_code(original)
        info = find_import(original, target, mask)
        if info is None:
            continue

        if info.kind == "esm_named":
            result.supported = False
            result.reason = (
                f"{path}: `{target}` is imported with named members, so the replacement's exports "
                "cannot be assumed to match."
            )
            return result

        if _uses_identifier(mask, replacement):
            result.supported = False
            result.reason = f"{path}: the identifier `{replacement}` is already in use."
            return result

        # The binding may only appear as the default value of a client parameter.
        default_pattern = re.compile(
            rf"(?P<param>[A-Za-z_$][\w$]*)\s*=\s*(?<![\w.$]){re.escape(info.binding)}\b"
        )
        parameters: list[str] = []
        for match in default_pattern.finditer(mask):
            parameters.append(match.group("param"))

        if not parameters:
            direct_call = re.search(rf"(?<![\w.$]){re.escape(info.binding)}\s*\.\s*[A-Za-z_$]", mask)
            reason = (
                f"{path}: `{target}` is used directly rather than behind an injectable client."
                if direct_call
                else f"{path}: `{target}` is not used as an injectable client."
            )
            result.supported = False
            result.reason = (
                f"{reason} The callback-based API cannot be migrated without changing behaviour, "
                "so this usage was left untouched."
            )
            return result

        # Every method used on the client must exist on the adapter.
        for parameter in parameters:
            for method in re.findall(rf"(?<![\w.$]){re.escape(parameter)}\s*\.\s*(?P<method>[A-Za-z_$][\w$]*)", mask):
                if method not in SUPPORTED_REQUEST_METHODS:
                    result.supported = False
                    result.reason = (
                        f"{path}: the client is used with `.{method}()`, which the `{replacement}` "
                        "adapter does not provide. This usage was not rewritten."
                    )
                    return result

        # Replace the import with the replacement plus the adapter.
        import_block = f'{info.match.group("prefix")}const {replacement} = require("{replacement}");\n{AXIOS_ADAPTER}'
        updated = original[: info.match.start()] + import_block + original[info.match.end() :]

        # Point every default binding at the adapter.
        for parameter in parameters:
            updated = re.sub(
                rf"(?P<head>[\w$]+\s*=\s*)(?<![\w.$]){re.escape(info.binding)}\b",
                rf"\g<head>httpClient",
                updated,
            )

        result.edits.append(FileEdit(path=path, original=original, updated=updated))

    if not result.edits:
        result.supported = False
        result.reason = "No file imports `request`, so there is nothing to migrate."
        return result

    result.warnings.append(
        "`axios` rejects on a non-2xx status; the adapter converts that back into a response so the "
        "existing status handling and error messages are unchanged."
    )
    return result


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------

CODEMODS = {
    "moment": moment_to_dayjs,
    "request": request_to_axios,
}


def apply_codemod(
    package: str,
    files: dict[str, str],
    *,
    replacement: str | None = None,
) -> CodemodResult:
    """Run the codemod registered for a package."""
    codemod = CODEMODS.get(package)
    if codemod is None:
        return CodemodResult(
            supported=False,
            reason=f"No deterministic codemod is registered for `{package}`.",
        )
    if replacement is None:
        return codemod(files)
    return codemod(files, replacement=replacement)  # type: ignore[call-arg]


def collect_source_files(root: Path, package: str) -> dict[str, str]:
    """Read the files that reference a package, so the codemod sees a small surface."""
    from .reachability import collect_source_files as collect

    contents: dict[str, str] = {}
    for source_file in collect(root):
        if f'"{package}"' in source_file.text or f"'{package}'" in source_file.text:
            contents[source_file.relative_path] = source_file.text
    return contents
