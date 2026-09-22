"""Application settings, loaded from environment / .env."""

from __future__ import annotations

import secrets
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
    llm_provider: Literal["anthropic", "openai", "gemini", "ollama"] = "anthropic"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""

    anthropic_chat_model: str = "claude-haiku-4-5-20251001"
    anthropic_vision_model: str = "claude-sonnet-5"
    openai_chat_model: str = "gpt-4o-mini"
    openai_vision_model: str = "gpt-4o"
    gemini_chat_model: str = "gemini-2.0-flash"
    gemini_vision_model: str = "gemini-2.0-flash"

    #: Ollama runs locally and needs no key, which makes it the fallback that
    #: works on a laptop with no credits at all. Availability is decided by
    #: reaching the server, not by a setting - see `ChatProvider.available()`.
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_chat_model: str = "llama3.1"
    #: Seconds. A local model on CPU is slow; a demo that hangs is worse than one
    #: that says the model timed out.
    ollama_timeout_seconds: float = 60.0

    #: Turns of conversation kept per session. Bounded so a long chat cannot grow
    #: the prompt without limit.
    advisor_history_turns: int = 12

    #: Hard ceiling so a runaway tool-calling loop cannot burn the team's credits.
    max_tokens_per_session: int = 50_000

    # --- Storage ---
    database_url: str = "sqlite:///./advisor.db"
    catalog_dir: Path = REPO_ROOT / "data" / "catalog"
    upload_dir: Path = BACKEND_DIR / "uploads"
    #: One JSON file per student. Files rather than a table so a person can open
    #: a record and see exactly what the system believes and where it came from.
    student_record_dir: Path = BACKEND_DIR / "student_records"
    #: One JSON file per account, alongside the records.
    user_dir: Path = BACKEND_DIR / "accounts"
    #: Transcripts are small. This is a guard against a memory-exhausting upload,
    #: not a policy about document length.
    max_upload_bytes: int = 5 * 1024 * 1024

    # --- Authentication ---
    #: Signs access tokens. MUST be set in production - see `jwt_signing_secret`,
    #: which generates a throwaway value when this is empty so a fresh checkout
    #: runs without anyone inventing a secret to paste into a config file.
    #: There is deliberately no default value here: a committed default secret is
    #: a committed credential, and every deployment would share it.
    jwt_secret: str = ""
    #: Hours, not weeks. These tokens are stateless and cannot be revoked before
    #: they expire, so the lifetime IS the revocation window.
    access_token_ttl_minutes: int = 12 * 60

    #: Failed logins from one client against one address before a cooldown starts.
    #: Generous enough that a person mistyping their password never notices.
    login_max_failures: int = 5
    #: First cooldown. Doubles on each subsequent lockout for the same pair.
    login_cooldown_seconds: int = 30
    #: The ceiling on that doubling. Capped so a lockout is never permanent.
    login_cooldown_max_seconds: int = 900
    #: An idle pair is forgotten after this, clearing its history entirely.
    login_forget_after_seconds: int = 900

    # --- Web ---
    cors_origins: str = "http://localhost:5173"

    @property
    def jwt_signing_secret(self) -> str:
        """The configured secret, or a random one generated for this process.

        Falling back to a random value rather than a constant means an unconfigured
        deployment is inconvenient - everyone is signed out when it restarts - but
        never insecure, which is the right way round. A hardcoded fallback would be
        a published signing key.
        """
        if self.jwt_secret:
            return self.jwt_secret
        return _ephemeral_secret()

    @property
    def jwt_secret_is_ephemeral(self) -> bool:
        """True when no secret was configured. Surfaced on /health as a warning."""
        return not self.jwt_secret

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def llm_configured(self) -> bool:
        """False is fine. The dashboard and degree audit work with zero LLM calls,
        so an unconfigured or expired key can never break the core demo.

        Ollama reports True because it needs no credential; whether it is actually
        running is a question only a request can answer, and `/advisor/health`
        asks it.
        """
        return bool(self.chat_api_key) or self.llm_provider == "ollama"

    @property
    def chat_api_key(self) -> str:
        """The key for the selected provider. Empty for ollama, which needs none."""
        return {
            "anthropic": self.anthropic_api_key,
            "openai": self.openai_api_key,
            "gemini": self.gemini_api_key,
            "ollama": "",
        }[self.llm_provider]

    @property
    def chat_model(self) -> str:
        return {
            "anthropic": self.anthropic_chat_model,
            "openai": self.openai_chat_model,
            "gemini": self.gemini_chat_model,
            "ollama": self.ollama_chat_model,
        }[self.llm_provider]


@lru_cache
def _ephemeral_secret() -> str:
    """One random secret per process, created on first use."""
    return secrets.token_urlsafe(48)


@lru_cache
def get_settings() -> Settings:
    return Settings()
