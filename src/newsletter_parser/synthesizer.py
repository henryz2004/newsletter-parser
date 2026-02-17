"""Stage 3: Final synthesis of extracted items into a cohesive Markdown briefing."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import anthropic
import markdown

from newsletter_parser.config import Settings
from newsletter_parser.extractor import ExtractedItem
from newsletter_parser.prompts import (
    SYNTHESIS_SYSTEM,
    SYNTHESIS_USER,
    SYNTHESIS_ITEM_TEMPLATE,
)

logger = logging.getLogger(__name__)


def synthesize_briefing(
    items: list[ExtractedItem], settings: Settings
) -> tuple[str, str]:
    """Produce a Markdown briefing from extracted newsletter items.

    Returns:
        A tuple of (markdown_text, html_text).
    """
    if not items:
        md = _empty_briefing()
        html = _md_to_html(md)
        return md, html

    # Prioritize and cap items to avoid overwhelming synthesis
    items = _prioritize_items(items, settings.max_synthesis_items)

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    # Build the items block for the prompt (truncate individual content)
    max_content_chars = 1500
    item_blocks = []
    for item in items:
        content = item.summary_text[:max_content_chars]
        if len(item.summary_text) > max_content_chars:
            content += "..."
        item_blocks.append(
            SYNTHESIS_ITEM_TEMPLATE.format(
                source=item.source_name,
                topics=", ".join(item.topics) if item.topics else "General",
                category=item.category,
                content=content,
                link=item.link_url or "N/A",
            )
        )

    user_msg = SYNTHESIS_USER.format(items_block="\n".join(item_blocks))

    try:
        response = client.messages.create(
            model=settings.synthesis_model,
            max_tokens=4096,
            system=SYNTHESIS_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        md_text = response.content[0].text
    except Exception:
        logger.exception("Synthesis API call failed; falling back to raw list")
        md_text = _fallback_briefing(items)

    # Append source references with Gmail deep links
    sources_md = _build_sources_section(items)
    if sources_md:
        md_text += "\n\n" + sources_md

    html_text = _md_to_html(md_text)
    return md_text, html_text


def build_subject() -> str:
    """Generate the email subject line for the briefing."""
    now = datetime.now(timezone.utc)
    hour = now.hour
    period = "Morning" if hour < 14 else "Evening"
    date_str = now.strftime("%B %d, %Y")
    return f"Newsletter Briefing — {period}, {date_str}"


# ── Internal helpers ─────────────────────────────────────────────────────────


def _prioritize_items(
    items: list[ExtractedItem], max_items: int
) -> list[ExtractedItem]:
    """Sort items by priority (high_relevance first) and cap at max_items."""
    priority = {"high_relevance": 0, "general_info": 1}
    sorted_items = sorted(items, key=lambda it: priority.get(it.category, 2))

    if len(sorted_items) > max_items:
        logger.info(
            "Capping synthesis input from %d to %d items",
            len(sorted_items),
            max_items,
        )
        sorted_items = sorted_items[:max_items]

    return sorted_items


def _md_to_html(md_text: str) -> str:
    """Convert Markdown to a styled HTML email body.

    Uses inline styles and table-based layout for maximum compatibility
    across Gmail, Apple Mail, and Outlook.
    """
    body_html = markdown.markdown(
        md_text,
        extensions=["extra", "smarty"],
    )

    # Inline-style the markdown-generated elements for Gmail compatibility
    body_html = _inline_styles(body_html)

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%A, %B %d, %Y")
    year = now.strftime("%Y")

    return f"""\
<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<title>Newsletter Briefing</title>
<!--[if mso]>
<style type="text/css">
  body, table, td {{ font-family: Arial, Helvetica, sans-serif !important; }}
</style>
<![endif]-->
</head>
<body style="margin:0; padding:0; background-color:#f4f4f7; -webkit-text-size-adjust:100%; -ms-text-size-adjust:100%;">

<!-- Outer wrapper table -->
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#f4f4f7;">
<tr><td align="center" style="padding:24px 16px;">

<!-- Inner content table (600px max) -->
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px; width:100%; background-color:#ffffff; border-radius:8px; overflow:hidden; box-shadow:0 1px 3px rgba(0,0,0,0.08);">

<!-- Header -->
<tr>
<td style="background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%); padding:32px 40px 28px 40px; text-align:left;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
  <tr>
  <td>
    <p style="margin:0 0 4px 0; font-family:Arial,Helvetica,sans-serif; font-size:11px; letter-spacing:2px; text-transform:uppercase; color:#7ec8e3; font-weight:600;">Newsletter Intelligence</p>
    <h1 style="margin:0 0 8px 0; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif; font-size:26px; font-weight:700; color:#ffffff; line-height:1.2;">Daily Briefing</h1>
    <p style="margin:0; font-family:Arial,Helvetica,sans-serif; font-size:13px; color:#a8b2d1;">{date_str}</p>
  </td>
  </tr>
  </table>
</td>
</tr>

<!-- Body content -->
<tr>
<td style="padding:32px 40px 16px 40px; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif; font-size:16px; line-height:1.7; color:#2d3436;">
{body_html}
</td>
</tr>

<!-- Divider -->
<tr>
<td style="padding:0 40px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
  <tr><td style="border-top:1px solid #e8e8e8; height:1px; line-height:1px; font-size:1px;">&nbsp;</td></tr>
  </table>
</td>
</tr>

<!-- Footer -->
<tr>
<td style="padding:20px 40px 28px 40px; text-align:center;">
  <p style="margin:0; font-family:Arial,Helvetica,sans-serif; font-size:12px; color:#999; line-height:1.5;">
    Curated by Newsletter Intelligence Agent
  </p>
</td>
</tr>

</table>
<!-- /Inner content table -->

</td></tr>
</table>
<!-- /Outer wrapper table -->

</body>
</html>"""


def _inline_styles(html: str) -> str:
    """Add inline styles to HTML elements for email client compatibility."""
    import re

    FONT = "-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif"

    # Section headers
    html = re.sub(
        r"<h1([^>]*)>",
        lambda m: f'<h1{m.group(1)} style="margin:0 0 16px 0; font-family:{FONT}; font-size:24px; font-weight:700; color:#1a1a2e; line-height:1.3;">',
        html,
    )
    html = re.sub(
        r"<h2([^>]*)>",
        lambda m: f'<h2{m.group(1)} style="margin:28px 0 12px 0; padding-bottom:8px; border-bottom:2px solid #e8e8e8; font-family:{FONT}; font-size:20px; font-weight:700; color:#1a1a2e; line-height:1.3;">',
        html,
    )
    html = re.sub(
        r"<h3([^>]*)>",
        lambda m: f'<h3{m.group(1)} style="margin:20px 0 8px 0; font-family:Arial,Helvetica,sans-serif; font-size:16px; font-weight:700; color:#2d3436; line-height:1.4;">',
        html,
    )

    # Paragraphs
    html = re.sub(
        r"<p([^>]*)>",
        lambda m: f'<p{m.group(1)} style="margin:0 0 16px 0; font-family:{FONT}; font-size:16px; line-height:1.7; color:#2d3436;">',
        html,
    )

    # Links
    html = html.replace(
        "<a ",
        '<a style="color:#0f3460; text-decoration:underline; text-underline-offset:2px;" ',
    )

    # Bold text
    html = re.sub(
        r"<strong([^>]*)>",
        lambda m: f'<strong{m.group(1)} style="color:#1a1a2e; font-weight:700;">',
        html,
    )

    # Unordered lists
    html = re.sub(
        r"<ul([^>]*)>",
        lambda m: f'<ul{m.group(1)} style="margin:0 0 16px 0; padding-left:20px; font-family:{FONT}; font-size:16px; line-height:1.7; color:#2d3436;">',
        html,
    )
    html = re.sub(
        r"<li([^>]*)>",
        lambda m: f'<li{m.group(1)} style="margin:0 0 8px 0; padding-left:4px;">',
        html,
    )

    # Blockquotes
    html = re.sub(
        r"<blockquote([^>]*)>",
        lambda m: f'<blockquote{m.group(1)} style="margin:16px 0; padding:12px 20px; border-left:3px solid #0f3460; background-color:#f8f9fa; font-style:italic; color:#555;">',
        html,
    )

    # Horizontal rules — styled as section dividers
    html = re.sub(
        r"<hr\s*/?>",
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:24px 0;"><tr><td style="border-top:1px solid #e8e8e8; height:1px; line-height:1px; font-size:1px;">&nbsp;</td></tr></table>',
        html,
    )

    return html


def _empty_briefing() -> str:
    """Return Markdown for when there are no items to report."""
    return (
        "## No Updates Today\n\n"
        "No new newsletter content was found since the last briefing. "
        "Check back next time!"
    )


def _build_sources_section(items: list[ExtractedItem]) -> str:
    """Build a Markdown sources section with Gmail deep links."""
    # Deduplicate by email_id (in case multiple items share the same email)
    seen: set[str] = set()
    lines = ["## Sources"]
    for item in items:
        if not item.email_id or item.email_id in seen:
            continue
        seen.add(item.email_id)
        gmail_link = f"https://mail.google.com/mail/u/0/#inbox/{item.email_id}"
        subject = item.email_subject or item.source_name
        lines.append(f"- [{subject}]({gmail_link}) — *{item.source_name}*")
    if len(lines) == 1:
        return ""
    return "\n".join(lines)


def _fallback_briefing(items: list[ExtractedItem]) -> str:
    """Produce a simple bullet-point fallback if the synthesis model fails."""
    lines = ["## Newsletter Briefing (Fallback)\n"]
    for item in items:
        link_part = f" — [link]({item.link_url})" if item.link_url else ""
        lines.append(f"- **{item.source_name}**: {item.summary_text[:200]}{link_part}")
    return "\n".join(lines)
