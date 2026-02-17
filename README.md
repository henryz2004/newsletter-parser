# Newsletter Parser

An AI agent that reads your Gmail newsletters, filters out the noise, and sends you a single beautifully formatted briefing email with everything that matters.

![Pipeline](https://img.shields.io/badge/pipeline-Gmail_%E2%86%92_Triage_%E2%86%92_Extract_%E2%86%92_Synthesize-blue)
![Python](https://img.shields.io/badge/python-3.11+-green)
![License](https://img.shields.io/badge/license-MIT-yellow)

## What It Does

If you subscribe to a lot of newsletters, your inbox is probably a mess. This tool:

1. **Fetches** unread emails from your Gmail `category:updates` using Gmail's importance filter as a pre-filter
2. **Triages** each email with Claude Haiku — separating real editorial newsletters from receipts, shipping alerts, password resets, and other transactional junk
3. **Extracts** content from the newsletters that pass triage, including following links to fetch full articles
4. **Synthesizes** everything into a single, topic-grouped briefing using Claude Sonnet — not a list of summaries, but a cohesive analyst-style brief
5. **Sends** the briefing as a styled HTML email to your inbox, with Gmail deep links back to every source email
6. **Cleans up** your inbox — marks junk as read, moves important newsletters to a "Newsletter Briefing" label

The result looks like a morning intelligence brief written by a smart analyst who read all your newsletters for you.

## Example Output

The briefing groups content by theme across all your newsletters:

> ### AI Model Wars Heat Up
> The AI landscape saw major developments this week with competing releases from the leading labs. According to *The AI Collective Newsletter*, Anthropic launched **Opus 4.6** featuring new "agent teams" capabilities, while OpenAI responded with **GPT-5.3-Codex**...

Each briefing ends with a **Sources** section linking directly to the original emails in Gmail.

## Architecture

```
Gmail (category:updates, unread, important)
  --> Stage 1: Triage (Claude Haiku) — cheap/fast classification in batches of 20
  --> Stage 2: Extract — HTML stripping, link following, token-budget chunking
  --> Stage 3: Synthesize (Claude Sonnet) — topic-grouped markdown briefing + styled HTML
  --> Send briefing, mark junk as read, label kept newsletters
```

**Cost:** Roughly $0.01–0.05 per run depending on email volume. Haiku handles the bulk work (triage + chunking), Sonnet only runs once for the final synthesis.

## Setup

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- A Google Cloud project with the Gmail API enabled
- An [Anthropic API key](https://console.anthropic.com/)

### 1. Clone and install

```bash
git clone https://github.com/henryz2004/newsletter-parser.git
cd newsletter-parser
uv sync
```

### 2. Set up Gmail API credentials

Follow the steps in [SETUP.md](SETUP.md) to create a Google Cloud project, enable the Gmail API, and download your `credentials.json`.

Then authenticate:

```bash
uv run newsletter-parser setup
```

This opens a browser for OAuth consent. After granting access, a `token.json` is saved locally.

### 3. Configure

```bash
cp .env.example .env
```

Edit `.env`:

```bash
# Required
ANTHROPIC_API_KEY=sk-ant-your-key-here

# Customize your interests (used for triage priority)
RELEVANCE_TOPICS=AI orchestration,startup strategy,systems design

# Optional: Gmail query override (remove is:important if too aggressive)
# GMAIL_QUERY=category:updates is:unread is:important
```

### 4. Run

```bash
# First run (processes last 7 days of newsletters)
uv run newsletter-parser run

# Dry run — preview without sending email
uv run newsletter-parser run --dry-run

# Save briefing to a file
uv run newsletter-parser run --output briefing.md

# Override lookback window
uv run newsletter-parser run --lookback-days 14

# Debug: dump fetched emails and triage decisions
uv run newsletter-parser run --dump-emails /tmp/emails.txt --dump-triage /tmp/triage.txt

# Verbose logging
uv run newsletter-parser -v run
```

### 5. Schedule (macOS)

Create a launchd plist at `~/Library/LaunchAgents/com.newsletter-parser.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.newsletter-parser</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/uv</string>
        <string>run</string>
        <string>newsletter-parser</string>
        <string>run</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/path/to/newsletter-parser</string>
    <key>StartCalendarInterval</key>
    <array>
        <dict>
            <key>Hour</key><integer>7</integer>
            <key>Minute</key><integer>0</integer>
        </dict>
        <dict>
            <key>Hour</key><integer>19</integer>
            <key>Minute</key><integer>0</integer>
        </dict>
    </array>
    <key>StandardOutPath</key>
    <string>/path/to/newsletter-parser/logs/launchd.log</string>
    <key>StandardErrorPath</key>
    <string>/path/to/newsletter-parser/logs/launchd.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
```

Update the paths, then load it:

```bash
mkdir -p logs
launchctl load ~/Library/LaunchAgents/com.newsletter-parser.plist
```

On Linux, use cron instead:

```bash
0 7,19 * * * cd /path/to/newsletter-parser && uv run newsletter-parser run >> logs/cron.log 2>&1
```

## Configuration

All settings are in `.env` (see [.env.example](.env.example)):

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | (required) | Your Anthropic API key |
| `TRIAGE_MODEL` | `claude-haiku-4-5-20251001` | Model for triage + chunk summarization |
| `SYNTHESIS_MODEL` | `claude-sonnet-4-20250514` | Model for final briefing synthesis |
| `RELEVANCE_TOPICS` | `AI orchestration, fragrance design, arbitrage/DeFi` | Comma-separated high-priority topics |
| `GMAIL_QUERY` | `category:updates is:unread is:important` | Gmail search query |
| `RECIPIENT_EMAIL` | (authenticated user) | Override briefing recipient |
| `INITIAL_LOOKBACK_DAYS` | `7` | Days to look back on first run |
| `TOKEN_BUDGET` | `4000` | Max tokens before chunking kicks in |

## How It Works

**Triage** uses a carefully tuned prompt that acts as a newsletter-vs-transactional separator. It keeps editorial newsletters (Substack, Beehiiv, curated digests) and aggressively discards receipts, shipping alerts, password resets, sports scores, Q&A digests, and SaaS announcements. The prompt took 6 iterations to get right.

**Extraction** strips HTML, scores and follows the best content link for high-relevance items, and uses a sliding-window chunker with 25% overlap when content exceeds the token budget.

**Synthesis** doesn't just list summaries — it groups content by theme across newsletters and writes a cohesive narrative, like a morning brief from an analyst who read everything for you.

**Resilience** is built into every stage: triage failure defaults to discard, extraction failure falls back to the email snippet, synthesis failure produces a bullet-point summary. The Gmail fetcher retries rate-limited requests automatically.

## Project Structure

```
src/newsletter_parser/
  main.py          # CLI + pipeline orchestration
  config.py        # Pydantic settings from .env
  gmail.py         # OAuth, fetch, send, label management
  triage.py        # Batch classification with Haiku
  extractor.py     # HTML stripping, link following, chunking
  synthesizer.py   # Markdown + styled HTML briefing generation
  prompts.py       # All LLM prompt templates
  state.py         # SQLite dedup + run history
```
