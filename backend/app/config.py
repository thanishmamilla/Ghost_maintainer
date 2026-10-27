"""Centralised configuration loaded from environment variables / backend/.env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_ROOT.parent


class Settings(BaseSettings):
    """Runtime settings. Every value has a safe default so the app boots with no .env."""

    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # API surface
    frontend_origin: str = "http://localhost:5173"
    log_level: str = "INFO"

    # GitHub / PR
    github_token: str = ""
    github_webhook_secret: str = ""
    dry_run: bool = True

    # Optional LLM (never required for the core workflow)
    llm_api_key: str = ""
    llm_model: str = ""
    llm_base_url: str = ""

    # External metadata
    request_timeout_seconds: float = 10.0
    metadata_concurrency: int = 8
    metadata_cache_ttl_seconds: int = 900

    # Sandbox
    sandbox_timeout_seconds: int = 180
    sandbox_mode: str = "auto"  # auto | docker | local_restricted
    allow_untrusted_local_execution: bool = False

    # Filesystem
    workspaces_dir: Path = Field(default=BACKEND_ROOT / ".workspaces")
    demo_repo_path: Path = Field(default=PROJECT_ROOT / "demo-rotten-app")
    offline_fixtures: bool = False

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key and self.llm_model)

    @property
    def github_configured(self) -> bool:
        return bool(self.github_token)

    @property
    def real_pr_mode(self) -> bool:
        """Real PRs require both a token and an explicit DRY_RUN=false."""
        return self.github_configured and not self.dry_run


@lru_cache
def get_settings() -> Settings:
    return Settings()