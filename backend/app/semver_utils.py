"""Minimal semver parsing and comparison.

The project has no runtime dependency on a semver library, and the comparison rules
needed here are narrow: order two versions, read the major component, detect
prereleases, and pick a fixed version. Keeping this in-tree avoids pulling in a
dependency whose own supply chain would then need auditing.
"""

from __future__ import annotations

import re
from functools import total_ordering

_VERSION_RE = re.compile(
    r"^v?(?P<major>0|[1-9]\d*)"
    r"(?:\.(?P<minor>0|[1-9]\d*))?"
    r"(?:\.(?P<patch>0|[1-9]\d*))?"
    r"(?:-(?P<prerelease>[0-9A-Za-z.-]+))?"
    r"(?:\+(?P<build>[0-9A-Za-z.-]+))?$"
)

# A range that pins an exact version, e.g. "1.2.3" or "=1.2.3".
_EXACT_RE = re.compile(r"^\s*[=v]?\s*(?P<version>\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)\s*$")


class InvalidVersion(ValueError):
    """Raised when a string cannot be interpreted as a semantic version."""


@total_ordering
class Version:
    """A comparable semantic version."""

    __slots__ = ("major", "minor", "patch", "prerelease", "build", "raw")

    def __init__(self, major: int, minor: int, patch: int, prerelease: str = "", build: str = "", raw: str = ""):
        self.major = major
        self.minor = minor
        self.patch = patch
        self.prerelease = prerelease
        self.build = build
        self.raw = raw or f"{major}.{minor}.{patch}"

    # -- construction ---------------------------------------------------

    @classmethod
    def parse(cls, value: str) -> "Version":
        match = _VERSION_RE.match(value.strip())
        if not match:
            raise InvalidVersion(f"Not a semantic version: {value!r}")
        return cls(
            major=int(match.group("major")),
            minor=int(match.group("minor") or 0),
            patch=int(match.group("patch") or 0),
            prerelease=match.group("prerelease") or "",
            build=match.group("build") or "",
            raw=value.strip(),
        )

    @classmethod
    def try_parse(cls, value: str | None) -> "Version | None":
        if not value:
            return None
        try:
            return cls.parse(value)
        except InvalidVersion:
            return None

    # -- comparison -----------------------------------------------------

    @property
    def is_prerelease(self) -> bool:
        return bool(self.prerelease)

    def _key(self) -> tuple:
        # A prerelease sorts before the matching release (semver §11).
        return (self.major, self.minor, self.patch, 0 if self.prerelease else 1)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() == other._key()

    def __lt__(self, other: "Version") -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        if self._key() != other._key():
            return self._key() < other._key()
        return _prerelease_key(self.prerelease) < _prerelease_key(other.prerelease)

    def __hash__(self) -> int:
        return hash(self._key())

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Version({self.raw!r})"

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        return f"{base}-{self.prerelease}" if self.prerelease else base


def _prerelease_key(prerelease: str) -> tuple:
    """Order prerelease identifiers: numeric ones compare numerically, others lexically."""
    if not prerelease:
        return (1,)  # a release outranks any prerelease of the same core version
    parts: list[tuple] = []
    for identifier in prerelease.split("."):
        if identifier.isdigit():
            parts.append((0, int(identifier), ""))
        else:
            parts.append((1, 0, identifier))
    return (0, tuple(parts))


def compare(left: str, right: str) -> int:
    """Return -1, 0 or 1 comparing two versions."""
    a = Version.parse(left)
    b = Version.parse(right)
    if a < b:
        return -1
    if a > b:
        return 1
    return 0


def is_at_least_major_behind(installed: str, latest: str) -> bool:
    """True when `installed` is at least one major version behind `latest`."""
    current = Version.try_parse(installed)
    newest = Version.try_parse(latest)
    if current is None or newest is None:
        return False
    return newest.major > current.major


def exact_version_from_range(value: str) -> str | None:
    """Extract an exact version from a declared range, if the range pins one.

    Ranges such as ``^4.17.15`` or ``~1.2.3`` describe a *range*, not an installed
    version, so they are deliberately not resolved here.
    """
    match = _EXACT_RE.match(value or "")
    return match.group("version") if match else None


def sort_versions(versions: list[str]) -> list[str]:
    """Sort version strings ascending, dropping unparseable entries."""
    parsed = [(v, Version.try_parse(v)) for v in versions]
    usable = [(raw, ver) for raw, ver in parsed if ver is not None]
    usable.sort(key=lambda item: item[1])
    return [raw for raw, _ in usable]


def highest_version(versions: list[str], *, allow_prerelease: bool = False) -> str | None:
    """Return the highest version from a list, or None when nothing parses."""
    candidates = [v for v in versions if allow_prerelease or not (Version.try_parse(v) and Version.parse(v).is_prerelease)]
    ordered = sort_versions(candidates)
    return ordered[-1] if ordered else None


def same_major(left: str, right: str) -> bool:
    """True when two versions share a major component."""
    a = Version.try_parse(left)
    b = Version.try_parse(right)
    if a is None or b is None:
        return False
    return a.major == b.major


def latest_stable(versions: list[str]) -> str | None:
    """Highest non-prerelease version."""
    return highest_version(versions, allow_prerelease=False)
