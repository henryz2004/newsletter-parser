# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Newsletter Parser is an automated agent that fetches Gmail newsletters, triages them by relevance using AI, extracts key content (including following links), and synthesizes everything into a consolidated daily briefing email. It uses a two-model strategy: Claude Haiku for cheap/fast triage and chunking, Claude Sonnet for high-quality synthesis.

## Commands

```bash
# Install dependencies
uv sync

# Run the full pipeline
uv run newsletter-parser run

# Dry-run (preview without sending email)
uv run newsletter-parser run --dry-run

# Setup Gmail OAuth (first-time, opens browser)
uv run newsletter-parser setup

# Verbose logging
uv run newsletter-parser -v run
```

No test suite exists yet. The `tests/` directory is empty.

## Architecture

Three-stage pipeline orchestrated by `main.py`:

```
Gmail (category:updates, unread)
  → Stage 1: Triage (triage.py, Haiku) — classifies emails by relevance, batches of 20
  → Stage 2: Extract (extractor.py) — strips HTML, follows links for high-relevance items, chunks large content with token budget
  → Stage 3: Synthesize (synthesizer.py, Sonnet) — generates topic-grouped Markdown briefing, converts to styled HTML email
  → Send via Gmail, mark originals as read
```

**Key modules in `src/newsletter_parser/`:**

- `config.py` — Pydantic-based settings loaded from `.env` (models, topics, token budget)
- `state.py` — SQLite state in `~/.newsletter-parser/state.db` for deduplication and run history
- `gmail.py` — OAuth2 auth with token caching, fetch/send/mark-read operations
- `prompts.py` — All LLM prompt templates (triage, chunk summarization, synthesis)
- `triage.py` — Batch classification returning category/score/topics per email
- `extractor.py` — HTML→text, smart link scoring/fetching, tiktoken-based chunking with overlap
- `synthesizer.py` — Produces (Markdown, HTML) briefing grouped by topic

**Resilience pattern:** Each stage has graceful fallbacks — triage failure defaults to general_info, extraction failure falls back to email snippet, synthesis failure produces bullet-point summary.

## Configuration

Environment variables (see `.env.example`):
- `ANTHROPIC_API_KEY` (required)
- `TRIAGE_MODEL`, `SYNTHESIS_MODEL` — model overrides
- `RELEVANCE_TOPICS` — comma-separated interest topics for triage
- `TOKEN_BUDGET` — max tokens per extraction context (default 4000)
- `RECIPIENT_EMAIL`, `INITIAL_LOOKBACK_DAYS` — optional overrides

Secrets (`credentials.json`, `token.json`, `.env`) are gitignored.
