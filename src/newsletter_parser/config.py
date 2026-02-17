"""Application configuration loaded from environment variables / .env file."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = Path.home() / ".newsletter-parser"


class Settings(BaseSettings):
    """All configurable knobs for the newsletter parser pipeline."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── LLM ──────────────────────────────────────────────────────────────
    anthropic_api_key: str = Field(description="Anthropic API key")
    triage_model: str = Field(
        default="claude-haiku-4-5-20251001",
        description="Model used for Stage 1 triage (cheap & fast)",
    )
    synthesis_model: str = Field(
        default="claude-sonnet-4-20250514",
        description="Model used for Stage 3 synthesis (higher quality)",
    )

    # ── Relevance ────────────────────────────────────────────────────────
    relevance_topics: list[str] = Field(
        default=["AI orchestration", "fragrance design", "arbitrage/DeFi"],
        description="Topics considered high-relevance during triage",
    )

    # ── Gmail ────────────────────────────────────────────────────────────
    gmail_query: str = Field(
        default="category:updates is:unread is:important",
        description="Base Gmail search query; 'after:{epoch}' is appended automatically",
    )
    recipient_email: str | None = Field(
        default=None,
        description="Override recipient email; defaults to the authenticated user",
    )

    # ── Pipeline tuning ──────────────────────────────────────────────────
    initial_lookback_days: int = Field(
        default=7,
        description="How many days to look back on the very first run",
    )
    token_budget: int = Field(
        default=4000,
        description="Max tokens of combined context before chunking kicks in",
    )
    triage_score_threshold: float = Field(
        default=0.5,
        description="Minimum relevance score to keep an email after triage",
    )
    max_per_sender: int = Field(
        default=3,
        description="Max emails to keep from any single sender (top N by score)",
    )
    max_synthesis_items: int = Field(
        default=25,
        description="Max items to feed into the synthesis prompt",
    )

    # ── Paths (derived, not from env) ────────────────────────────────────
    credentials_path: Path = Field(
        default=PROJECT_ROOT / "credentials.json",
        description="Path to Google OAuth credentials JSON",
    )
    token_path: Path = Field(
        default=PROJECT_ROOT / "token.json",
        description="Path to cached OAuth token",
    )
    db_path: Path = Field(
        default=DATA_DIR / "state.db",
        description="Path to the SQLite state database",
    )


def get_settings() -> Settings:
    """Instantiate and return validated settings."""
    return Settings()  # type: ignore[call-arg]
