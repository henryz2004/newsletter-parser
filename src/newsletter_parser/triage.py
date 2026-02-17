"""Stage 1: Low-cost triage of emails using Claude Haiku."""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass

import anthropic

from newsletter_parser.config import Settings
from newsletter_parser.gmail import RawEmail
from newsletter_parser.prompts import TRIAGE_SYSTEM, TRIAGE_USER, TRIAGE_EMAIL_TEMPLATE

logger = logging.getLogger(__name__)

# Maximum emails per triage batch (keeps prompt size manageable)
BATCH_SIZE = 20


@dataclass
class TriageResult:
    """Triage classification for a single email."""

    email: RawEmail
    category: str          # "high_relevance", "general_info", or "discard"
    relevance_score: float
    topics: list[str]
    reason: str


def triage_emails(
    emails: list[RawEmail],
    settings: Settings,
    return_all: bool = False,
) -> list[TriageResult]:
    """Classify emails by relevance using Claude Haiku.

    Batches emails to reduce API round-trips. Returns only non-discarded items
    (those with relevance_score >= threshold) unless *return_all* is True.

    When *return_all* is True, returns every triage result (kept and discarded).
    """
    if not emails:
        return []

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    all_results: list[TriageResult] = []

    for batch_start in range(0, len(emails), BATCH_SIZE):
        batch = emails[batch_start : batch_start + BATCH_SIZE]
        results = _triage_batch(batch, client, settings)
        all_results.extend(results)

    # Filter: keep non-discarded items above the score threshold
    kept = [
        r for r in all_results
        if r.category != "discard"
        and r.relevance_score >= settings.triage_score_threshold
    ]

    # Deduplicate by sender — keep top N per sender
    kept = _deduplicate_by_sender(kept, settings.max_per_sender)

    logger.info(
        "Triage: %d/%d emails kept (%d high_relevance, %d general_info, %d discarded)",
        len(kept),
        len(emails),
        sum(1 for r in kept if r.category == "high_relevance"),
        sum(1 for r in kept if r.category == "general_info"),
        len(all_results) - len(kept),
    )

    return all_results if return_all else kept


def _triage_batch(
    batch: list[RawEmail],
    client: anthropic.Anthropic,
    settings: Settings,
) -> list[TriageResult]:
    """Send a batch of emails to Haiku for triage classification."""
    topics_str = ", ".join(settings.relevance_topics)

    # Build the email block — subject + first ~200 tokens of preview
    email_blocks = []
    for i, email in enumerate(batch, 1):
        preview = email.snippet or email.body_text[:600]
        email_blocks.append(
            TRIAGE_EMAIL_TEMPLATE.format(
                index=i,
                subject=email.subject,
                sender=email.sender,
                preview=preview[:600],
            )
        )

    user_msg = TRIAGE_USER.format(
        count=len(batch),
        emails_block="\n".join(email_blocks),
    )

    try:
        response = client.messages.create(
            model=settings.triage_model,
            max_tokens=4096,
            system=TRIAGE_SYSTEM.format(topics=topics_str),
            messages=[{"role": "user", "content": user_msg}],
        )

        raw_text = response.content[0].text
        classifications = _parse_triage_response(raw_text, batch)
        for c in classifications:
            logger.debug(
                "  [%s] score=%.2f subject=%r reason=%r",
                c.category,
                c.relevance_score,
                c.email.subject[:60],
                c.reason[:80],
            )
        return classifications

    except Exception:
        logger.exception("Triage API call failed for batch of %d", len(batch))
        # On failure, treat all as general_info so nothing is silently dropped
        return [
            TriageResult(
                email=e,
                category="general_info",
                relevance_score=0.5,
                topics=[],
                reason="Triage failed; defaulting to general_info",
            )
            for e in batch
        ]


def _parse_triage_response(
    raw_text: str, batch: list[RawEmail]
) -> list[TriageResult]:
    """Parse the JSON array returned by the triage model."""
    # Strip markdown code fences if present
    text = raw_text.strip()
    if text.startswith("```"):
        # Remove first and last lines (fences)
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]).strip()

    try:
        items = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Failed to parse triage JSON, treating batch as discard")
        return [
            TriageResult(
                email=e,
                category="discard",
                relevance_score=0.0,
                topics=[],
                reason="JSON parse failed; defaulting to discard",
            )
            for e in batch
        ]

    results: list[TriageResult] = []
    for i, email in enumerate(batch):
        if i < len(items):
            item = items[i]
            results.append(
                TriageResult(
                    email=email,
                    category=item.get("category", "general_info"),
                    relevance_score=float(item.get("relevance_score", 0.5)),
                    topics=item.get("topics", []),
                    reason=item.get("reason", ""),
                )
            )
        else:
            # Model returned fewer items than expected — default to discard
            # so transactional emails don't leak through
            results.append(
                TriageResult(
                    email=email,
                    category="discard",
                    relevance_score=0.0,
                    topics=[],
                    reason="Missing from model output; defaulting to discard",
                )
            )

    return results


# ── Sender deduplication ─────────────────────────────────────────────────────


def _normalize_sender(sender: str) -> str:
    """Extract a canonical sender key from the From header.

    'Newsletter Name <noreply@example.com>' → 'noreply@example.com'
    """
    match = re.search(r"<([^>]+)>", sender)
    if match:
        return match.group(1).lower().strip()
    return sender.lower().strip()


def _deduplicate_by_sender(
    results: list[TriageResult], max_per_sender: int
) -> list[TriageResult]:
    """If a sender has more than *max_per_sender* emails, keep only the top N by score."""
    by_sender: dict[str, list[TriageResult]] = defaultdict(list)
    for r in results:
        key = _normalize_sender(r.email.sender)
        by_sender[key].append(r)

    kept: list[TriageResult] = []
    for sender_key, group in by_sender.items():
        if len(group) > max_per_sender:
            group.sort(key=lambda r: r.relevance_score, reverse=True)
            logger.debug(
                "Sender '%s' has %d emails; keeping top %d",
                sender_key,
                len(group),
                max_per_sender,
            )
            kept.extend(group[:max_per_sender])
        else:
            kept.extend(group)

    return kept
