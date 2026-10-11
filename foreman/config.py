"""Settings from .env (pydantic-settings).

Settings are the only global state in Foreman. Keys are optional so the offline test
suite and `dev --fake` run on a fresh checkout without any secrets.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Every variable from `.env.example`, typed, with the documented defaults."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Jev (TypeSafe) via Vercel AI Gateway
    ai_gateway_api_key: SecretStr | None = None
    jev_base_url: str = "https://ai-gateway.vercel.sh/typesafe"
    jev_model: str = "typesafe-ai/jev"
    jev_max_concurrency: int = 32
    jev_max_rps: float = 60
    jev_max_state_tokens: int = 28_000
    jev_timeout_s: float = 20
    jev_price_per_m_input: float = 0.042

    # Light LLM: DeepSeek V4 Flash on Fireworks
    fireworks_api_key: SecretStr | None = None
    llm_base_url: str = "https://api.fireworks.ai/inference/v1"
    llm_model: str = "accounts/fireworks/models/deepseek-v4-flash"
    llm_max_concurrency: int = 8
    llm_timeout_s: float = 30  # decide() (fallback): short prompts, tiny answers
    llm_json_timeout_s: float = 90  # json() (proposals, planner): longer outputs
    llm_decide_max_tokens: int = 256
    llm_json_max_tokens: int = 4096
    # DeepSeek V4 on Fireworks reasons by default (~4k hidden tokens, ~40 s per proposal);
    # "none" turns that off. "low" is ignored by the model. Empty = don't send the param.
    llm_reasoning_effort: str = "none"
    llm_price_per_m_input: float = 0.0
    llm_price_per_m_output: float = 0.0

    # Run defaults
    confidence_threshold: float = 0.7
    runs_dir: str = "runs"
    log_level: str = "INFO"

    @field_validator("llm_reasoning_effort", mode="before")
    @classmethod
    def _effort_none_is_empty(cls, v: object) -> object:
        return "" if v is None else v

    @field_validator("ai_gateway_api_key", "fireworks_api_key", mode="before")
    @classmethod
    def _empty_is_none(cls, v: object) -> object:
        # An empty `KEY=` line in .env means "not set", not "set to the empty string".
        return None if v == "" else v


@lru_cache
def get_settings() -> Settings:
    """Cached so every module sees the same settings without re-reading `.env`."""
    return Settings()
