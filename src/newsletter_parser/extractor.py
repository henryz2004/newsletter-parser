"""Stage 2: HTML stripping, link extraction, content fetching, and chunked summarization."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

import anthropic
import httpx
import tiktoken
from bs4 import BeautifulSoup

from newsletter_parser.config import Settings
from newsletter_parser.prompts import CHUNK_SUMMARY_SYSTEM, CHUNK_SUMMARY_USER
from newsletter_parser.triage import TriageResult

logger = logging.getLogger(__name__)

# Invisible Unicode characters that pollute extracted text
_INVISIBLE_UNICODE = re.compile(
    r"[\u200b\u200c\u200d\u200e\u200f"   # zero-width spaces / joiners / direction marks
    r"\u00ad"                              # soft hyphen
    r"\u2060\u2061\u2062\u2063\u2064"      # invisible operators
    r"\ufeff"                              # BOM / zero-width no-break space
    r"\u00a0"                              # non-breaking space → regular space
    r"\u034f\u061c\u115f\u1160"            # misc invisible
    r"\u17b4\u17b5\uffa0]",
)


def _strip_invisible_unicode(text: str) -> str:
    """Remove invisible Unicode characters that clutter extracted content."""
    return _INVISIBLE_UNICODE.sub("", text)


# URL patterns to skip during link scoring
_SKIP_PATTERNS = re.compile(
    r"(unsubscribe|manage.preferences|mailto:|twitter\.com|facebook\.com|"
    r"instagram\.com|linkedin\.com/share|youtube\.com|t\.co/|bit\.ly|"
    r"list-manage\.com|mailchimp\.com|campaign-archive|view.in.browser|"
    r"privacy.policy|terms.of.service|\.png|\.jpg|\.gif|\.svg)",
    re.IGNORECASE,
)

# Domains that are almost always tracking / infrastructure
_SKIP_DOMAINS = {
    "email.mg", "clicks.mlsend", "click.convertkit-mail",
    "trk.klclick", "t.dripemail2", "links.beehiiv",
}


@dataclass
class ExtractedItem:
    """Fully extracted and summarized newsletter item."""

    source_name: str
    topics: list[str]
    category: str
    summary_text: str
    link_url: str | None = None
    full_content: str = ""
    email_id: str = ""
    email_subject: str = ""


def extract_items(
    triaged: list[TriageResult], settings: Settings
) -> list[ExtractedItem]:
    """Process triaged emails: strip HTML, follow links for high-relevance items,
    chunk and summarize if needed.
    """
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    enc = tiktoken.get_encoding("cl100k_base")
    items: list[ExtractedItem] = []

    for result in triaged:
        try:
            item = _extract_single(result, client, enc, settings)
            items.append(item)
        except Exception:
            logger.exception(
                "Extraction failed for '%s'; using snippet fallback",
                result.email.subject,
            )
            items.append(
                ExtractedItem(
                    source_name=_source_name(result.email.sender),
                    topics=result.topics,
                    category=result.category,
                    summary_text=result.email.snippet,
                    email_id=result.email.id,
                    email_subject=result.email.subject,
                )
            )

    logger.info("Extracted %d items", len(items))
    return items


def _extract_single(
    result: TriageResult,
    client: anthropic.Anthropic,
    enc: tiktoken.Encoding,
    settings: Settings,
) -> ExtractedItem:
    """Extract content from a single triaged email."""
    email = result.email

    # 1. Strip HTML → plain text
    body = _strip_html(email.body_html) if email.body_html else email.body_text

    # 2. For high-relevance items, attempt to follow the primary link
    link_url: str | None = None
    link_content: str = ""

    if result.category == "high_relevance":
        best_link = _find_best_link(email.body_html)
        if best_link:
            link_url = best_link
            link_content = _fetch_link_content(best_link)

    # 3. Combine content
    combined = body
    if link_content:
        combined += "\n\n--- Linked Article ---\n\n" + link_content

    # 4. Chunk and summarize if over budget
    token_count = len(enc.encode(combined))
    if token_count > settings.token_budget:
        summary = _chunked_summarize(combined, client, enc, settings)
    else:
        summary = combined

    return ExtractedItem(
        source_name=_source_name(email.sender),
        topics=result.topics,
        category=result.category,
        summary_text=summary,
        link_url=link_url,
        full_content=combined,
        email_id=email.id,
        email_subject=email.subject,
    )


# ── HTML Processing ──────────────────────────────────────────────────────────


def _strip_html(html: str) -> str:
    """Convert HTML email body to clean plain text."""
    soup = BeautifulSoup(html, "lxml")

    # Remove script, style, and head tags
    for tag in soup(["script", "style", "head", "meta", "link"]):
        tag.decompose()

    text = soup.get_text(separator="\n", strip=True)
    text = _strip_invisible_unicode(text)

    # Collapse excessive whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _source_name(sender: str) -> str:
    """Extract a human-readable source name from the From header."""
    # "Newsletter Name <noreply@example.com>" → "Newsletter Name"
    match = re.match(r'^"?([^"<]+)"?\s*<', sender)
    if match:
        return match.group(1).strip()
    return sender.split("@")[0]


# ── Link Extraction & Scoring ────────────────────────────────────────────────


def _find_best_link(html: str) -> str | None:
    """Find the most relevant content link in an HTML email body."""
    if not html:
        return None

    soup = BeautifulSoup(html, "lxml")
    candidates: list[tuple[float, str]] = []

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        if not href.startswith(("http://", "https://")):
            continue

        score = _score_link(href, a_tag.get_text(strip=True))
        if score > 0:
            candidates.append((score, href))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def _score_link(url: str, anchor_text: str) -> float:
    """Score a link for content relevance (higher = better). Returns 0 to skip."""
    # Skip known bad patterns
    if _SKIP_PATTERNS.search(url):
        return 0.0

    parsed = urlparse(url)
    domain = parsed.netloc.lower()

    # Skip tracking / infrastructure domains
    for skip in _SKIP_DOMAINS:
        if skip in domain:
            return 0.0

    score = 0.5  # base score

    # Boost links with meaningful anchor text (not "click here")
    if anchor_text and len(anchor_text) > 10:
        score += 0.3

    # Boost links with path depth (likely articles, not homepages)
    path_parts = [p for p in parsed.path.split("/") if p]
    if len(path_parts) >= 2:
        score += 0.2

    # Slight boost for common article domains
    if any(
        d in domain
        for d in ["medium.com", "substack.com", "arxiv.org", "github.com"]
    ):
        score += 0.1

    return score


# ── Link Content Fetching ────────────────────────────────────────────────────


def _fetch_link_content(url: str) -> str:
    """Fetch and extract article text from a URL."""
    try:
        with httpx.Client(
            timeout=15.0,
            follow_redirects=True,
            headers={"User-Agent": "NewsletterParser/1.0"},
        ) as http:
            resp = http.get(url)
            resp.raise_for_status()
    except Exception:
        logger.warning("Failed to fetch link: %s", url)
        return ""

    content_type = resp.headers.get("content-type", "")
    if "html" not in content_type:
        return ""

    soup = BeautifulSoup(resp.text, "lxml")

    # Remove non-content elements
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "iframe"]):
        tag.decompose()

    # Try to find the main article content
    article = soup.find("article") or soup.find("main") or soup.find("body")
    if article is None:
        return ""

    text = article.get_text(separator="\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Cap at ~8k chars to avoid huge payloads
    text = _strip_invisible_unicode(text[:8000])
    return text


# ── Chunked Summarization ───────────────────────────────────────────────────


def _chunked_summarize(
    text: str,
    client: anthropic.Anthropic,
    enc: tiktoken.Encoding,
    settings: Settings,
) -> str:
    """Split text into chunks and summarize each with Haiku, then concatenate."""
    tokens = enc.encode(text)
    chunk_size = settings.token_budget
    # Use 25% overlap for sliding window
    stride = int(chunk_size * 0.75)

    summaries: list[str] = []
    pos = 0

    while pos < len(tokens):
        chunk_tokens = tokens[pos : pos + chunk_size]
        chunk_text = enc.decode(chunk_tokens)

        try:
            response = client.messages.create(
                model=settings.triage_model,  # Use cheap model for chunk summarization
                max_tokens=512,
                system=CHUNK_SUMMARY_SYSTEM,
                messages=[
                    {"role": "user", "content": CHUNK_SUMMARY_USER.format(chunk=chunk_text)}
                ],
            )
            summaries.append(response.content[0].text)
        except Exception:
            logger.warning("Chunk summarization failed; using raw truncation")
            summaries.append(chunk_text[:500] + "...")

        pos += stride

    return "\n\n".join(summaries)
