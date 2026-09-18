"""Application settings, loaded from environment / .env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- LLM provider ---
    # Provider-neutral on purpose: the team applies for Anthropic AND OpenAI student
    # credits, then flips this one value to whichever came through.
    llm_provider: Literal["anthropic", "openai"] = "anthropic"
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    anthropic_chat_model: str = "claude-haiku-4-5-20251001"
    anthropic_vision_model: str = "claude-sonnet-5"
    openai_chat_model: str = "gpt-4o-mini"
    openai_vision_model: str = "gpt-4o"

    #: Hard ceiling so a runaway tool-calling loop cannot burn the team's credits.
    max_tokens_per_session: int = 50_000

    # --- Storage ---
    database_url: str = "sqlite:///./advisor.db"
    catalog_dir: Path = REPO_ROOT / "data" / "catalog"
    upload_dir: Path = BACKEND_DIR / "uploads"

    # --- Web ---
    cors_origins: str = "http://localhost:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def llm_configured(self) -> bool:
        """False is fine. The dashboard and degree audit work with zero LLM calls,
        so an unconfigured or expired key can never break the core demo."""
        key = self.anthropic_api_key if self.llm_provider == "anthropic" else self.openai_api_key
        return bool(key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
