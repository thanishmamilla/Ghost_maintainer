"""Capture real npm/OSV/GitHub responses for the demo packages as offline fixtures.

Run once while the network is reachable. The captured JSON lets the scanner, health
engine and fixer run deterministically during judging, even if a registry rate-limits
or a service is down. The capture is written to backend/app/fixtures/ and is committed.

Usage:
    python -m app.fixtures_capture
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx

from .config import get_settings

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"

DEMO_PACKAGES: list[tuple[str, str]] = [
    ("moment", "2.29.1"),
    ("request", "2.88.2"),
    ("lodash", "4.17.15"),
    ("minimist", "1.2.0"),
    ("jsonwebtoken", "3.2.2"),
    ("semver", "7.8.5"),
]

# The migration targets. Only clean versions are captured: the tests assert that
# a replacement does not introduce its own advisories.
EXTRA_PACKAGES: list[tuple[str, str]] = [
    ("dayjs", "1.11.23"),
    ("axios", "1.20.0"),
]

NPM_URL = "https://registry.npmjs.org/{name}"
OSV_URL = "https://api.osv.dev/v1/query"


def _npm_path(name: str) -> Path:
    return FIXTURE_DIR / "npm" / f"{name.replace('/', '%2f')}.json"


def _osv_path(name: str, version: str) -> Path:
    return FIXTURE_DIR / "osv" / f"{name.replace('/', '%2f')}@{version}.json"


async def fetch_json(client: httpx.AsyncClient, method: str, url: str, **kwargs: Any) -> dict:
    response = await client.request(method, url, **kwargs)
    response.raise_for_status()
    return response.json()


def _trim_npm(payload: dict, installed_versions: tuple[str, ...]) -> dict:
    """Keep only the fields the health engine reads.

    A full npm document is several hundred kilobytes per package; the registry
    exposes the same information in a compact form once the irrelevant version
    history is removed.
    """
    versions: dict[str, dict] = {}
    for wanted in installed_versions:
        entry = (payload.get("versions") or {}).get(wanted)
        if entry:
            versions[wanted] = {
                "version": entry.get("version"),
                "deprecated": entry.get("deprecated"),
                "license": entry.get("license"),
                "repository": entry.get("repository"),
            }

    latest = (payload.get("dist-tags") or {}).get("latest")
    latest_entry = (payload.get("versions") or {}).get(latest) or {}
    times = payload.get("time") or {}

    return {
        "name": payload.get("name"),
        "dist-tags": payload.get("dist-tags"),
        "time": {key: times.get(key) for key in {latest, *installed_versions, "created", "modified"} if key in times},
        "repository": payload.get("repository") or latest_entry.get("repository"),
        "license": latest_entry.get("license"),
        "versions": versions,
        "deprecated_latest": latest_entry.get("deprecated"),
    }


def _github_path(slug: str) -> Path:
    return FIXTURE_DIR / "github" / f"{slug.replace('/', '%2f')}.json"


def _trim_github(payload: dict) -> dict:
    """Keep only the repository fields the health engine reads."""
    license_field = payload.get("license")
    return {
        "full_name": payload.get("full_name"),
        "archived": payload.get("archived"),
        "disabled": payload.get("disabled"),
        "pushed_at": payload.get("pushed_at"),
        "updated_at": payload.get("updated_at"),
        "html_url": payload.get("html_url"),
        "default_branch": payload.get("default_branch"),
        "stargazers_count": payload.get("stargazers_count"),
        "open_issues_count": payload.get("open_issues_count"),
        "archived_at": payload.get("archived_at"),
        "license": license_field if isinstance(license_field, dict) else None,
    }


def _slug_for(repository_url: str | None) -> str | None:
    """Derive an owner/repo slug from an npm repository URL."""
    if not repository_url:
        return None
    from .health import parse_github_slug

    return parse_github_slug(repository_url)


async def capture_github(client: httpx.AsyncClient, slugs: set[str]) -> None:
    (FIXTURE_DIR / "github").mkdir(parents=True, exist_ok=True)
    for slug in sorted(slugs):
        try:
            payload = await fetch_json(client, "GET", f"https://api.github.com/repos/{slug}")
            _github_path(slug).write_text(json.dumps(_trim_github(payload), indent=1), encoding="utf-8")
            print(f"gh    {slug:24} captured")
        except Exception as exc:  # noqa: BLE001 - capture is best effort
            print(f"gh    {slug:24} FAILED: {exc}")


async def capture() -> None:
    settings = get_settings()
    (FIXTURE_DIR / "npm").mkdir(parents=True, exist_ok=True)
    (FIXTURE_DIR / "osv").mkdir(parents=True, exist_ok=True)

    timeout = httpx.Timeout(settings.request_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": "ghost-maintainer/0.1"}) as client:
        slugs: set[str] = set()
        for name, version in {**dict(DEMO_PACKAGES), **dict(EXTRA_PACKAGES)}.items():
            try:
                payload = await fetch_json(client, "GET", NPM_URL.format(name=name))
                trimmed = _trim_npm(payload, tuple(v for n, v in DEMO_PACKAGES + EXTRA_PACKAGES if n == name))
                _npm_path(name).write_text(json.dumps(trimmed, indent=1), encoding="utf-8")
                slug = _slug_for(trimmed.get("repository", {}).get("url") if isinstance(trimmed.get("repository"), dict) else None)
                if slug:
                    slugs.add(slug)
                print(f"npm   {name:16} captured")
            except Exception as exc:  # noqa: BLE001 - capture is best effort
                print(f"npm   {name:16} FAILED: {exc}")

        for name, version in DEMO_PACKAGES + EXTRA_PACKAGES:
            try:
                payload = await fetch_json(
                    client,
                    "POST",
                    OSV_URL,
                    json={"package": {"name": name, "ecosystem": "npm"}, "version": version},
                )
                _osv_path(name, version).write_text(json.dumps(payload, indent=1), encoding="utf-8")
                count = len(payload.get("vulns") or [])
                print(f"osv   {name:16}@{version:10} captured ({count} vulns)")
            except Exception as exc:  # noqa: BLE001 - capture is best effort
                print(f"osv   {name:16}@{version:10} FAILED: {exc}")

        await capture_github(client, slugs)

    manifest = {
        "captured_at": "manual",
        "packages": sorted({name for name, _ in DEMO_PACKAGES + EXTRA_PACKAGES}),
    }
    (FIXTURE_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nfixtures written to {FIXTURE_DIR}")


def main() -> None:
    asyncio.run(capture())


if __name__ == "__main__":
    main()
