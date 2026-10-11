"""API-only settings (CORS, run limits), read from `.env` like the engine's settings.

Kept apart from `foreman.config.Settings` because only the server needs them; the CLI and
the tools never should.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
DEV_ORIGIN = "http://localhost:5173"  # Vite's dev server


class ApiSettings(BaseSettings):
    """`ALLOWED_ORIGINS` is a comma-separated list added to the Vite dev origin."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    allowed_origins: str = ""
    max_active_runs: int = 2
    max_items: int = 2000  # per API run; the 10k hero run goes through the CLI
    max_upload_mb: float = 50
    plans_dir: Path = REPO_ROOT / "data" / "plans"

    @property
    def cors_origins(self) -> list[str]:
        extra = [o.strip().rstrip("/") for o in self.allowed_origins.split(",") if o.strip()]
        return [DEV_ORIGIN, *[o for o in extra if o != DEV_ORIGIN]]


@lru_cache
def get_api_settings() -> ApiSettings:
    return ApiSettings()
