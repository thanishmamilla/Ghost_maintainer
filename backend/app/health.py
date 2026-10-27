"""Dependency health: registry, advisory and repository metadata.

Queries npm, OSV, GitHub, deps.dev and the npm downloads API concurrently, with
per-scan caching, timeouts and graceful degradation. A single unavailable
provider never fails the whole scan; the affected packages simply carry fewer
facts and say so.

Every response is validated defensively: advisory schemas vary between
providers and between releases, and malformed input must never be interpreted
as a finding.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx

from .config import get_settings
from .cvss import base_score, rating_for_score, score_from_any
from .models import (
    Dependency,
    DependencyReport,
    MetadataStatus,
    PackageHealth,
    ReachabilityClass,
    ReachabilityResult,
    Severity,
    Vulnerability,
)
from .rot_score import compute_rot_score
from .semver_utils import Version

logger = logging.getLogger("ghost_maintainer.health")

NPM_REGISTRY_URL = "https://registry.npmjs.org/{name}"
OSV_QUERY_URL = "https://api.osv.dev/v1/query"
GITHUB_REPO_URL = "https://api.github.com/repos/{slug}"
DEPS_DEV_URL = "https://api.deps.dev/v3alpha/systems/npm/packages/{name}"
NPM_DOWNLOADS_URL = "https://api.npmjs.org/downloads/point/last-week/{name}"
SCORECARD_URL = "https://api.securityscorecards.dev/projects/github.com/{slug}"

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures"

_SEVERITY_ALIASES = {
    "CRITICAL": Severity.CRITICAL,
    "HIGH": Severity.HIGH,
    "MODERATE": Severity.MEDIUM,
    "MEDIUM": Severity.MEDIUM,
    "LOW": Severity.LOW,
    "NONE": Severity.UNKNOWN,
}

_GITHUB_SLUG_PATTERNS = (
    re.compile(r"^git\+https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"),
    re.compile(r"^git://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"),
    re.compile(r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"),
    re.compile(r"^github:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"),
    re.compile(r"^(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)$"),
)


# ---------------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------------


class MetadataCache:
    """A small TTL cache so repeated lookups within a scan hit the network once."""

    def __init__(self, ttl_seconds: int = 900) -> None:
        self._ttl = ttl_seconds
        self._entries: dict[str, tuple[float, Any]] = {}
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any | None:
        entry = self._entries.get(key)
        if entry is None:
            self.misses += 1
            return None
        stored_at, value = entry
        if time.monotonic() - stored_at >= self._ttl:
            self.misses += 1
            self._entries.pop(key, None)
            return None
        self.hits += 1
        return value

    def set(self, key: str, value: Any) -> None:
        self._entries[key] = (time.monotonic(), value)

    def clear(self) -> None:
        self._entries.clear()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def parse_github_slug(repository_url: str | None) -> str | None:
    """Extract ``owner/repo`` from the many shapes npm uses for repository URLs."""
    if not repository_url:
        return None
    candidate = repository_url.strip()
    for pattern in _GITHUB_SLUG_PATTERNS:
        match = pattern.match(candidate)
        if match:
            owner = match.group("owner")
            repo = match.group("repo")
            if owner.lower() in {"", "github", "gitlab", "bitbucket"}:
                return None
            return f"{owner}/{repo}"
    return None


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalise_severity(payload: dict) -> tuple[Severity, str, float | None]:
    """Determine an advisory's severity, its provenance and a numeric score.

    A human-readable rating published by the advisory database is preferred. A
    CVSS vector is only used when no rating exists. Nothing is invented: an
    advisory with neither is reported as ``unknown``.
    """
    database_specific = payload.get("database_specific")
    if isinstance(database_specific, dict):
        raw = database_specific.get("severity")
        if isinstance(raw, str):
            mapped = _SEVERITY_ALIASES.get(raw.strip().upper())
            if mapped is not None:
                return mapped, "database_specific", None
        cvss = database_specific.get("cvss")
        numeric = score_from_any(cvss)
        if numeric is not None:
            return Severity(rating_for_score(numeric)), "cvss_vector", numeric

    for entry in payload.get("severity") or []:
        if not isinstance(entry, dict):
            continue
        vector = entry.get("score")
        numeric = score_from_any(vector)
        if numeric is not None:
            return Severity(rating_for_score(numeric)), "cvss_vector", numeric

    numeric = score_from_any(payload.get("cvss"))
    if numeric is not None:
        return Severity(rating_for_score(numeric)), "cvss_vector", numeric

    return Severity.UNKNOWN, "unknown", None


def _extract_affected_functions(payload: dict) -> list[str]:
    """Collect function names an advisory says are affected, when stated."""
    functions: list[str] = []
    for affected in payload.get("affected") or []:
        if not isinstance(affected, dict):
            continue

        ecosystem_specific = affected.get("ecosystem_specific")
        if isinstance(ecosystem_specific, dict):
            for entry in ecosystem_specific.get("imports") or []:
                if isinstance(entry, dict):
                    for symbol in entry.get("symbols") or []:
                        if isinstance(symbol, str):
                            functions.append(symbol)

        database_specific = affected.get("database_specific")
        if isinstance(database_specific, dict):
            for function in database_specific.get("functions") or []:
                if isinstance(function, str):
                    functions.append(function)

    return sorted(set(functions))


def _extract_fixed_versions(payload: dict) -> list[str]:
    fixed: list[str] = []
    for affected in payload.get("affected") or []:
        if not isinstance(affected, dict):
            continue
        for range_ in affected.get("ranges") or []:
            if not isinstance(range_, dict):
                continue
            for event in range_.get("events") or []:
                if isinstance(event, dict) and isinstance(event.get("fixed"), str):
                    fixed.append(event["fixed"])
    return sorted(set(fixed))


def _extract_affected_ranges(payload: dict) -> list[str]:
    """Render the affected version ranges as readable strings."""
    rendered: list[str] = []
    for affected in payload.get("affected") or []:
        if not isinstance(affected, dict):
            continue
        for range_ in affected.get("ranges") or []:
            if not isinstance(range_, dict):
                continue
            events = [event for event in range_.get("events") or [] if isinstance(event, dict)]
            if not events:
                continue
            parts: list[str] = []
            for event in events:
                if "introduced" in event:
                    parts.append(f"introduced {event['introduced']}")
                if "fixed" in event:
                    parts.append(f"fixed {event['fixed']}")
                if "last_affected" in event:
                    parts.append(f"last affected {event['last_affected']}")
            if parts:
                rendered.append(", ".join(parts))
    return sorted(set(rendered))


def normalise_vulnerability(payload: dict) -> Vulnerability | None:
    """Turn a raw OSV record into a typed vulnerability.

    Returns ``None`` when the record has no usable identifier, because an
    advisory without an ID cannot be reported or checked against a fix.
    """
    if not isinstance(payload, dict):
        return None
    identifier = payload.get("id")
    if not isinstance(identifier, str) or not identifier:
        return None

    severity, severity_source, numeric = _normalise_severity(payload)
    aliases = [alias for alias in payload.get("aliases") or [] if isinstance(alias, str)]
    references = [
        reference.get("url")
        for reference in payload.get("references") or []
        if isinstance(reference, dict) and isinstance(reference.get("url"), str)
    ]
    summary = payload.get("summary") if isinstance(payload.get("summary"), str) else ""

    return Vulnerability(
        id=identifier,
        aliases=aliases,
        summary=summary,
        severity=severity,
        severity_source=severity_source,  # type: ignore[arg-type]
        cvss_score=numeric,
        affected_ranges=_extract_affected_ranges(payload),
        fixed_versions=_extract_fixed_versions(payload),
        references=references[:8],
        affected_functions=_extract_affected_functions(payload),
    )


# ---------------------------------------------------------------------------
# service
# ---------------------------------------------------------------------------


@dataclass
class EnrichmentResult:
    reports: list[DependencyReport] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class HealthService:
    """Fetches and normalises metadata for a set of dependencies."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        cache: MetadataCache | None = None,
    ) -> None:
        self.settings = get_settings()
        self.cache = cache or MetadataCache(self.settings.metadata_cache_ttl_seconds)
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> "HealthService":
        if self._client is None:
            timeout = httpx.Timeout(self.settings.request_timeout_seconds)
            headers = {
                "User-Agent": "ghost-maintainer/0.1 (+https://github.com/ghost-maintainer)",
                "Accept": "application/json",
            }
            if self.settings.github_configured:
                headers["Authorization"] = f"Bearer {self.settings.github_token}"
            self._client = httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True)
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("HealthService must be used as an async context manager.")
        return self._client

    # -- low level ------------------------------------------------------

    async def _get_json(self, url: str, *, headers: dict[str, str] | None = None) -> Any | None:
        try:
            response = await self.client.get(url, headers=headers)
            if response.status_code == 404:
                return None
            if response.status_code == 429:
                logger.warning("Rate limited by %s", url.split("/")[2])
                return None
            if response.status_code >= 400:
                logger.warning("HTTP %s from %s", response.status_code, url.split("/")[2])
                return None
            return response.json()
        except httpx.HTTPError as exc:
            logger.warning("Request to %s failed: %s", url, exc.__class__.__name__)
            return None
        except ValueError:
            logger.warning("Non-JSON response from %s", url)
            return None

    async def _post_json(self, url: str, body: dict) -> Any | None:
        try:
            response = await self.client.post(url, json=body)
            if response.status_code >= 400:
                logger.warning("HTTP %s from %s", response.status_code, url.split("/")[2])
                return None
            return response.json()
        except httpx.HTTPError as exc:
            logger.warning("Request to %s failed: %s", url, exc.__class__.__name__)
            return None
        except ValueError:
            logger.warning("Non-JSON response from %s", url)
            return None

    # -- fixtures -------------------------------------------------------

    def _fixture_npm(self, name: str) -> dict | None:
        path = FIXTURE_ROOT / "npm" / f"{name.replace('/', '%2f')}.json"
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _fixture_osv(self, name: str, version: str) -> dict | None:
        path = FIXTURE_ROOT / "osv" / f"{name.replace('/', '%2f')}@{version}.json"
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    # -- providers ------------------------------------------------------

    async def fetch_npm(self, name: str) -> dict | None:
        """Registry metadata for a package."""
        key = f"npm:{name}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        payload: dict | None = None
        if self.settings.offline_fixtures:
            payload = self._fixture_npm(name)
        else:
            payload = await self._get_json(NPM_REGISTRY_URL.format(name=name))
            if payload is None:
                payload = self._fixture_npm(name)
                if payload is not None:
                    logger.info("Using the captured npm fixture for %s", name)

        if payload is not None:
            self.cache.set(key, payload)
        return payload

    async def fetch_osv(self, name: str, version: str | None) -> tuple[list[Vulnerability], bool]:
        """Advisories affecting a specific version.

        Returns the advisories and whether the advisory service could actually
        be reached. The distinction matters: an empty list from a working
        service means "no known advisories", while an unreachable service must
        not be reported as a clean bill of health.
        """
        if not version:
            return [], False

        key = f"osv:{name}@{version}"
        cached = self.cache.get(key)
        if cached is not None:
            return list(cached[0]), bool(cached[1])

        payload: dict | None = None
        available = True
        if self.settings.offline_fixtures:
            payload = self._fixture_osv(name, version)
            available = payload is not None
        else:
            payload = await self._post_json(
                OSV_QUERY_URL,
                {"package": {"name": name, "ecosystem": "npm"}, "version": version},
            )
            if payload is None:
                available = False
                payload = self._fixture_osv(name, version)
                if payload is not None:
                    available = True
                    logger.info("Using the captured OSV fixture for %s@%s", name, version)

        vulnerabilities: list[Vulnerability] = []
        if payload is not None:
            for raw in payload.get("vulns") or []:
                normalised = normalise_vulnerability(raw)
                if normalised is not None:
                    vulnerabilities.append(normalised)
            vulnerabilities.sort(key=lambda vuln: vuln.id)

        self.cache.set(key, (vulnerabilities, available))
        return vulnerabilities, available

    def _fixture_github(self, slug: str) -> dict | None:
        path = FIXTURE_ROOT / "github" / f"{slug.replace('/', '%2f')}.json"
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    async def fetch_github(self, slug: str) -> dict | None:
        """Repository metadata for an owner/repo slug."""
        if not slug:
            return None
        key = f"github:{slug}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        payload: dict | None = None
        if self.settings.offline_fixtures:
            payload = self._fixture_github(slug)
        else:
            payload = await self._get_json(GITHUB_REPO_URL.format(slug=slug))
            if payload is None:
                payload = self._fixture_github(slug)
                if payload is not None:
                    logger.info("Using the captured GitHub fixture for %s", slug)

        if payload is not None:
            self.cache.set(key, payload)
        return payload

    async def fetch_downloads(self, name: str) -> int | None:
        """Last-week download count. Display only; never affects the score."""
        key = f"downloads:{name}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        payload = await self._get_json(NPM_DOWNLOADS_URL.format(name=name))
        count = None
        if isinstance(payload, dict) and isinstance(payload.get("downloads"), int):
            count = payload["downloads"]
            self.cache.set(key, count)
        return count

    async def fetch_deps_dev(self, name: str) -> dict | None:
        """deps.dev package metadata. Display only; never affects the score."""
        key = f"depsdev:{name}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        payload = await self._get_json(DEPS_DEV_URL.format(name=name))
        if payload is not None:
            self.cache.set(key, payload)
        return payload

    async def fetch_scorecard(self, slug: str) -> float | None:
        """OpenSSF Scorecard rating. Display only; never affects the score."""
        if not slug:
            return None
        key = f"scorecard:{slug}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        payload = await self._get_json(SCORECARD_URL.format(slug=slug))
        score = None
        if isinstance(payload, dict):
            try:
                score = float(payload["score"])
                self.cache.set(key, score)
            except (KeyError, TypeError, ValueError):
                score = None
        return score

    # -- composition ----------------------------------------------------

    def _npm_health(self, payload: dict | None, dependency: Dependency) -> PackageHealth:
        health = PackageHealth(metadata_status=MetadataStatus.UNAVAILABLE)
        if not isinstance(payload, dict):
            health.data_gaps.append("The npm registry could not be reached for this package.")
            return health

        dist_tags = payload.get("dist-tags")
        versions = payload.get("versions") if isinstance(payload.get("versions"), dict) else {}
        times = payload.get("time") if isinstance(payload.get("time"), dict) else {}

        latest = dist_tags.get("latest") if isinstance(dist_tags, dict) else None
        if isinstance(latest, str):
            health.latest_version = latest
        health.latest_published_at = _parse_timestamp(times.get(latest)) if latest else None

        installed = dependency.version
        if installed:
            health.published_at = _parse_timestamp(times.get(installed))
            installed_entry = versions.get(installed) if isinstance(versions, dict) else None
            if isinstance(installed_entry, dict):
                if "license" in installed_entry:
                    health.license = installed_entry.get("license") or health.license
                deprecated_value = installed_entry.get("deprecated")
                if deprecated_value is not None:
                    health.deprecated = bool(deprecated_value)
                    health.deprecated_message = str(deprecated_value) if deprecated_value else None

        if health.deprecated is None:
            latest_entry = versions.get(latest) if isinstance(versions, dict) else None
            if isinstance(latest_entry, dict) and latest_entry.get("deprecated") is not None:
                health.deprecated = bool(latest_entry["deprecated"])
                health.deprecated_message = (
                    str(latest_entry["deprecated"]) if latest_entry["deprecated"] else None
                )
            elif "deprecated_latest" in payload:
                value = payload.get("deprecated_latest")
                health.deprecated = bool(value)
                health.deprecated_message = str(value) if value else None

        repository = payload.get("repository")
        if isinstance(repository, dict):
            health.repository_url = repository.get("url") or health.repository_url
        elif isinstance(repository, str):
            health.repository_url = repository
        if not health.repository_url and isinstance(versions, dict) and isinstance(versions.get(latest), dict):
            entry_repository = versions[latest].get("repository")
            if isinstance(entry_repository, dict):
                health.repository_url = entry_repository.get("url")
        if not health.license and isinstance(payload.get("license"), str):
            health.license = payload["license"]

        health.metadata_status = MetadataStatus.PARTIAL
        return health

    def _apply_github(self, health: PackageHealth, payload: dict | None) -> None:
        if not isinstance(payload, dict):
            health.data_gaps.append("Repository metadata could not be retrieved from the GitHub API.")
            return
        health.repo_archived = payload.get("archived") if isinstance(payload.get("archived"), bool) else None
        health.repo_pushed_at = _parse_timestamp(payload.get("pushed_at"))
        if not health.repository_url and isinstance(payload.get("html_url"), str):
            health.repository_url = payload["html_url"]
        if not health.license and isinstance(payload.get("license"), dict):
            spdx = payload["license"].get("spdx_id")
            if isinstance(spdx, str) and spdx not in {"NOASSERTION", "null"}:
                health.license = spdx

    async def enrich_dependency(
        self,
        dependency: Dependency,
        *,
        reference_time: datetime | None = None,
    ) -> DependencyReport:
        """Gather all metadata for a single dependency."""
        data_gaps: list[str] = []

        npm_payload = await self.fetch_npm(dependency.name)
        health = self._npm_health(npm_payload, dependency)

        if health.latest_version and not dependency.version:
            health.data_gaps.append(
                "The installed version is unknown, so the version gap and publish age could not be assessed."
            )

        slug = parse_github_slug(health.repository_url)
        if slug:
            github_payload = await self.fetch_github(slug)
            self._apply_github(health, github_payload)
        else:
            data_gaps.append("No source repository could be identified, so maintainer activity is unknown.")

        vulnerabilities, advisories_available = await self.fetch_osv(dependency.name, dependency.version)
        if not dependency.version:
            data_gaps.append("Advisories were not queried because the installed version is unknown.")
        elif not advisories_available:
            data_gaps.append("The advisory service could not be reached, so known vulnerabilities are unknown.")

        # Enrichment only. These signals are displayed but never scored, and they
        # are skipped entirely in offline mode so a run stays reproducible.
        deps_dev: dict | None = None
        if not self.settings.offline_fixtures:
            health.downloads_last_week = await self.fetch_downloads(dependency.name)
            if slug:
                health.scorecard_score = await self.fetch_scorecard(slug)
            deps_dev = await self.fetch_deps_dev(dependency.name)

        if isinstance(deps_dev, dict) and not health.license:
            licenses = deps_dev.get("licenses")
            if isinstance(licenses, list) and licenses:
                first = licenses[0]
                if isinstance(first, str):
                    health.license = first

        health.data_gaps.extend(data_gaps)
        if health.metadata_status is MetadataStatus.UNAVAILABLE and vulnerabilities:
            health.metadata_status = MetadataStatus.PARTIAL
        elif health.metadata_status is not MetadataStatus.UNAVAILABLE:
            health.metadata_status = (
                MetadataStatus.OK
                if not health.data_gaps and health.repo_pushed_at is not None
                else MetadataStatus.PARTIAL
            )

        rot = compute_rot_score(
            health=health,
            vulnerabilities=vulnerabilities,
            installed_version=dependency.version,
            latest_version=health.latest_version,
            reference_time=reference_time,
            advisory_data_available=advisories_available,
        )

        return DependencyReport(
            name=dependency.name,
            version=dependency.version,
            version_source=dependency.version_source,
            declared_range=dependency.declared_range,
            is_dev=dependency.is_dev,
            latest_version=health.latest_version,
            repository_url=health.repository_url,
            license=health.license,
            health=health,
            rot=rot,
            vulnerabilities=vulnerabilities,
            reachability=ReachabilityResult(
                classification=ReachabilityClass.UNKNOWN,
                explanation="Source scanning has not run for this package yet.",
            ),
            fix_options=[],
        )

    async def enrich_all(
        self,
        dependencies: Iterable[Dependency],
        *,
        reference_time: datetime | None = None,
    ) -> EnrichmentResult:
        """Enrich every dependency concurrently, bounded by a concurrency limit."""
        dependency_list = list(dependencies)
        if not dependency_list:
            return EnrichmentResult()

        reference = reference_time or datetime.now(timezone.utc)
        semaphore = asyncio.Semaphore(self.settings.metadata_concurrency)
        errors: list[str] = []

        async def worker(dependency: Dependency) -> DependencyReport | None:
            async with semaphore:
                try:
                    return await self.enrich_dependency(dependency, reference_time=reference)
                except Exception as exc:  # noqa: BLE001 - one bad package must not fail the scan
                    logger.exception("Failed to enrich %s", dependency.name)
                    errors.append(f"{dependency.name}: metadata lookup failed ({exc.__class__.__name__}).")
                    return None

        results = await asyncio.gather(*(worker(dep) for dep in dependency_list))
        reports = [report for report in results if report is not None]
        reports.sort(key=lambda report: (-report.rot.value, report.name.lower()))
        return EnrichmentResult(reports=reports, errors=errors)
