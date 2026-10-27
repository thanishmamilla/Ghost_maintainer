"""Repository-specific review memory ("the learner").

The hackathon version keeps a small JSON store rather than a database. After a
pull request is merged, the issues that recur in a repository are recorded so a
later review can surface them first and the UI can show what this team keeps
getting wrong.

Nothing here trains a model. It records observed, attributable history.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import get_settings

logger = logging.getLogger("ghost_maintainer.learner")

_MAX_PATTERNS_PER_REPO = 200


@dataclass
class Pattern:
    """A recurring issue in one repository."""

    repo: str
    agent: str
    message: str
    count: int = 1
    last_seen: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "repo": self.repo,
            "agent": self.agent,
            "message": self.message,
            "count": self.count,
            "last_seen": self.last_seen,
        }


class Learner:
    """A thread-safe, file-backed store of repository-specific patterns."""

    def __init__(self, path: Path | None = None) -> None:
        settings = get_settings()
        self.path = path or (settings.workspaces_dir / "learner.json")
        self._lock = threading.Lock()
        self._cache: dict[str, list[Pattern]] | None = None

    # -- persistence ----------------------------------------------------

    def _load(self) -> dict[str, list[Pattern]]:
        if self._cache is not None:
            return self._cache
        if not self.path.is_file():
            self._cache = {}
            return self._cache
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("Could not read the learner store; starting empty.")
            self._cache = {}
            return self._cache

        repositories = payload.get("repositories") if isinstance(payload, dict) else None
        loaded: dict[str, list[Pattern]] = {}
        if isinstance(repositories, dict):
            for repo, entries in repositories.items():
                if not isinstance(entries, list):
                    continue
                patterns = []
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    message = entry.get("message")
                    agent = entry.get("agent")
                    if not isinstance(message, str) or not isinstance(agent, str):
                        continue
                    patterns.append(
                        Pattern(
                            repo=repo,
                            agent=agent,
                            message=message,
                            count=int(entry.get("count") or 1),
                            last_seen=str(entry.get("last_seen") or ""),
                        )
                    )
                loaded[repo] = patterns
        self._cache = loaded
        return loaded

    def _save(self, data: dict[str, list[Pattern]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "repositories": {
                repo: [pattern.to_dict() for pattern in patterns] for repo, patterns in data.items()
            }
        }
        try:
            self.path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        except OSError as exc:  # pragma: no cover - disk failure is non-fatal
            logger.warning("Could not persist the learner store: %s", exc)

    # -- public API -----------------------------------------------------

    def record(self, repo: str, *, agent: str, message: str) -> Pattern:
        """Record that an issue was accepted (for example, merged) in a repository."""
        with self._lock:
            data = self._load()
            patterns = data.setdefault(repo, [])
            normalised = message.strip().lower()
            for pattern in patterns:
                if pattern.agent == agent and pattern.message.strip().lower() == normalised:
                    pattern.count += 1
                    pattern.last_seen = datetime.now(timezone.utc).isoformat()
                    self._save(data)
                    return pattern

            pattern = Pattern(repo=repo, agent=agent, message=message.strip())
            patterns.insert(0, pattern)
            data[repo] = patterns[:_MAX_PATTERNS_PER_REPO]
            self._save(data)
            return pattern

    def patterns_for(self, repo: str, *, limit: int = 10) -> list[Pattern]:
        """The most frequent recent issues for a repository."""
        data = self._load()
        patterns = data.get(repo, [])
        return sorted(patterns, key=lambda pattern: pattern.count, reverse=True)[:limit]

    def prompt_hint(self, repo: str) -> str:
        """A short instruction block describing what this repository keeps getting wrong."""
        patterns = self.patterns_for(repo, limit=6)
        if not patterns:
            return ""
        lines = ["This repository has repeatedly had these issues; pay particular attention to them:"]
        for pattern in patterns:
            lines.append(f"- [{pattern.agent}] {pattern.message} (seen {pattern.count}x)")
        return "\n".join(lines)

    def clear(self, repo: str | None = None) -> None:
        with self._lock:
            data = self._load()
            if repo is None:
                data.clear()
            else:
                data.pop(repo, None)
            self._save(data)