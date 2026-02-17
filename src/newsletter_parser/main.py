"""CLI entrypoint and pipeline orchestrator for the Newsletter Intelligence Agent."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone

from newsletter_parser.config import get_settings, Settings
from newsletter_parser.extractor import extract_items
from newsletter_parser.gmail import GmailClient
from newsletter_parser.state import StateStore
from newsletter_parser.synthesizer import build_subject, synthesize_briefing
from newsletter_parser.triage import triage_emails

logger = logging.getLogger("newsletter_parser")


def main() -> None:
    """Parse CLI arguments and dispatch to the appropriate subcommand."""
    parser = argparse.ArgumentParser(
        prog="newsletter-parser",
        description="Newsletter Intelligence & Daily Briefing Agent",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug logging"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # ── setup ────────────────────────────────────────────────────────────
    subparsers.add_parser("setup", help="Authenticate with Gmail (OAuth flow)")

    # ── run ──────────────────────────────────────────────────────────────
    run_parser = subparsers.add_parser("run", help="Execute the full pipeline")
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the pipeline but don't send the email or update state",
    )
    run_parser.add_argument(
        "--output",
        metavar="FILE",
        help="Write markdown briefing to FILE (implies --dry-run)",
    )
    run_parser.add_argument(
        "--dump-emails",
        metavar="FILE",
        help="Write fetched email list (subject/sender/date) to FILE before triage",
    )
    run_parser.add_argument(
        "--dump-triage",
        metavar="FILE",
        help="Write triage results (kept & discarded with scores/reasons) to FILE",
    )
    run_parser.add_argument(
        "--lookback-days",
        type=int,
        metavar="N",
        help="Override lookback window (ignores state DB timestamp)",
    )

    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    settings = get_settings()

    if args.command == "setup":
        _cmd_setup(settings)
    elif args.command == "run":
        output_path = getattr(args, "output", None)
        dump_emails = getattr(args, "dump_emails", None)
        dump_triage = getattr(args, "dump_triage", None)
        lookback_days = getattr(args, "lookback_days", None)
        dry_run = args.dry_run or output_path is not None
        _cmd_run(
            settings,
            dry_run=dry_run,
            output_path=output_path,
            dump_emails_path=dump_emails,
            dump_triage_path=dump_triage,
            lookback_days_override=lookback_days,
        )


# ── Subcommands ──────────────────────────────────────────────────────────────


def _cmd_setup(settings: Settings) -> None:
    """Trigger the OAuth2 flow to authenticate with Gmail."""
    gmail = GmailClient(settings)
    gmail.authenticate()
    print("Authentication successful. token.json has been saved.")
    print()
    print("Suggested cron entry (twice daily at 7 AM and 7 PM):")
    print(
        f"  0 7,19 * * * cd {settings.credentials_path.parent} "
        "&& uv run newsletter-parser run >> /tmp/newsletter-parser.log 2>&1"
    )


def _cmd_run(
    settings: Settings,
    *,
    dry_run: bool = False,
    output_path: str | None = None,
    dump_emails_path: str | None = None,
    dump_triage_path: str | None = None,
    lookback_days_override: int | None = None,
) -> None:
    """Execute the full newsletter processing pipeline."""
    # 1. Initialize
    gmail = GmailClient(settings)
    gmail.authenticate()
    state = StateStore(settings.db_path)

    try:
        # 2. Determine time window
        if lookback_days_override is not None:
            since = datetime.now(timezone.utc) - timedelta(
                days=lookback_days_override
            )
            logger.info(
                "Lookback override — looking back %d days to %s",
                lookback_days_override,
                since.isoformat(),
            )
        elif (last_run := state.last_run_time()) is None:
            since = datetime.now(timezone.utc) - timedelta(
                days=settings.initial_lookback_days
            )
            logger.info(
                "First run — looking back %d days to %s",
                settings.initial_lookback_days,
                since.isoformat(),
            )
        else:
            since = last_run
            logger.info("Fetching emails since last run: %s", since.isoformat())

        # 3. Fetch
        emails = gmail.fetch_updates(since=since)

        # Dump fetched emails to file if requested
        if dump_emails_path and emails:
            from pathlib import Path

            lines = [f"Fetched {len(emails)} emails (query: {settings.gmail_query})\n"]
            for i, e in enumerate(emails, 1):
                lines.append(f"{i}. {e.subject}")
                lines.append(f"   From: {e.sender}")
                lines.append(f"   Date: {e.date}")
                lines.append(f"   Snippet: {e.snippet[:120]}")
                lines.append("")
            Path(dump_emails_path).write_text("\n".join(lines), encoding="utf-8")
            logger.info("Email list written to %s", dump_emails_path)

        if not emails:
            logger.info("No new emails found. Nothing to do.")
            state.record_run(0)
            return

        # 4. Deduplicate against already-processed messages
        unprocessed = [e for e in emails if not state.is_processed(e.id)]
        if not unprocessed:
            logger.info("All fetched emails already processed. Nothing to do.")
            state.record_run(0)
            return

        logger.info(
            "%d new emails to process (%d already processed)",
            len(unprocessed),
            len(emails) - len(unprocessed),
        )

        # 5. Stage 1: Triage (always get all results for inbox management)
        all_triage = triage_emails(unprocessed, settings, return_all=True)
        triaged = [
            r for r in all_triage
            if r.category != "discard"
            and r.relevance_score >= settings.triage_score_threshold
        ]
        kept_ids = {r.email.id for r in triaged}
        discarded_ids = [r.email.id for r in all_triage if r.email.id not in kept_ids]

        if dump_triage_path:
            from pathlib import Path

            lines = [f"Triage results: {len(triaged)} kept / {len(all_triage)} total\n"]
            lines.append("=" * 60)
            lines.append("KEPT")
            lines.append("=" * 60)
            for r in sorted(all_triage, key=lambda r: r.relevance_score, reverse=True):
                if r.email.id in kept_ids:
                    lines.append(
                        f"  [{r.category}] score={r.relevance_score:.2f}  {r.email.subject}"
                    )
                    lines.append(f"    From: {r.email.sender}")
                    lines.append(f"    Topics: {', '.join(r.topics) or '(none)'}")
                    lines.append(f"    Reason: {r.reason}")
                    lines.append("")
            lines.append("=" * 60)
            lines.append("DISCARDED")
            lines.append("=" * 60)
            for r in sorted(all_triage, key=lambda r: r.relevance_score, reverse=True):
                if r.email.id not in kept_ids:
                    lines.append(
                        f"  [{r.category}] score={r.relevance_score:.2f}  {r.email.subject}"
                    )
                    lines.append(f"    From: {r.email.sender}")
                    lines.append(f"    Reason: {r.reason}")
                    lines.append("")
            Path(dump_triage_path).write_text("\n".join(lines), encoding="utf-8")
            logger.info("Triage results written to %s", dump_triage_path)

        if not triaged:
            logger.info("All emails were discarded by triage. No briefing needed.")
            if not dry_run:
                gmail.mark_as_read(discarded_ids)
                for e in unprocessed:
                    state.mark_processed(e.id)
            state.record_run(len(unprocessed))
            return

        # 6. Stage 2: Extract
        extracted = extract_items(triaged, settings)

        # 7. Stage 3: Synthesize
        md_text, html_text = synthesize_briefing(extracted, settings)

        # 8. Deliver
        subject = build_subject()

        if dry_run:
            if output_path:
                from pathlib import Path

                Path(output_path).write_text(md_text, encoding="utf-8")
                logger.info("Markdown briefing written to %s", output_path)
                # Also write HTML preview alongside the markdown
                html_path = output_path.rsplit(".", 1)[0] + ".html"
                Path(html_path).write_text(html_text, encoding="utf-8")
                logger.info("HTML preview written to %s", html_path)
            else:
                print("=" * 60)
                print(f"SUBJECT: {subject}")
                print("=" * 60)
                print(md_text)
                print("=" * 60)
            print("(Dry run — email not sent, state not updated)")
        else:
            gmail.send_briefing(html_text, subject)

            # 9. Inbox management
            #    - Discarded emails → mark as read (clear inbox noise)
            #    - Kept emails → move to "Newsletter Briefing" label (stay unread)
            gmail.mark_as_read(discarded_ids)
            label_id = gmail.ensure_label("Newsletter Briefing")
            gmail.move_to_label(list(kept_ids), label_id)

            # 10. Update state
            for e in unprocessed:
                state.mark_processed(e.id)
            state.record_run(len(unprocessed))

            logger.info("Pipeline complete. Briefing sent.")

    except Exception:
        logger.exception("Pipeline failed")
        sys.exit(1)
    finally:
        state.close()


if __name__ == "__main__":
    main()
