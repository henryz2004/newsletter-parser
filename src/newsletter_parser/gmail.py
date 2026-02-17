"""Gmail API integration: authentication, fetching updates, sending briefings."""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build, Resource
from googleapiclient.http import BatchHttpRequest

from newsletter_parser.config import Settings

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]


@dataclass
class RawEmail:
    """A single email fetched from Gmail."""

    id: str
    subject: str
    sender: str
    date: str
    snippet: str
    body_html: str
    body_text: str


class GmailClient:
    """Thin wrapper around the Gmail API."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._service: Resource | None = None

    # ── Authentication ───────────────────────────────────────────────────

    def authenticate(self) -> None:
        """Run the OAuth2 flow (or refresh cached token) and build the service."""
        creds: Credentials | None = None

        if self._settings.token_path.exists():
            creds = Credentials.from_authorized_user_file(
                str(self._settings.token_path), SCOPES
            )

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                logger.info("Refreshing expired OAuth token")
                creds.refresh(Request())
            else:
                if not self._settings.credentials_path.exists():
                    raise FileNotFoundError(
                        f"credentials.json not found at {self._settings.credentials_path}. "
                        "See SETUP.md for instructions."
                    )
                logger.info("Starting OAuth authorization flow")
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self._settings.credentials_path), SCOPES
                )
                creds = flow.run_local_server(
                    port=8080,
                    prompt="consent",
                    access_type="offline",
                )

            # Persist for next run
            self._settings.token_path.write_text(creds.to_json())
            logger.info("Token saved to %s", self._settings.token_path)

        self._service = build("gmail", "v1", credentials=creds)
        logger.info("Gmail service ready")

    # ── Fetch ────────────────────────────────────────────────────────────

    @property
    def service(self) -> Resource:
        if self._service is None:
            raise RuntimeError("Call authenticate() first")
        return self._service

    def fetch_updates(
        self, since: datetime | None = None, base_query: str | None = None
    ) -> list[RawEmail]:
        """Fetch emails matching *base_query* (default from settings).

        Args:
            since: If provided, only fetch emails after this timestamp.
                   On first run this is typically 7 days ago.
            base_query: Gmail search query override. Defaults to
                        ``settings.gmail_query``.
        """
        query = base_query or self._settings.gmail_query
        if since is not None:
            epoch = int(since.timestamp())
            query += f" after:{epoch}"

        logger.info("Gmail query: %s", query)

        # Collect all message IDs first (lightweight list calls)
        msg_ids: list[str] = []
        page_token: str | None = None

        while True:
            result = (
                self.service.users()
                .messages()
                .list(userId="me", q=query, pageToken=page_token)
                .execute()
            )
            msg_ids.extend(m["id"] for m in result.get("messages", []))

            page_token = result.get("nextPageToken")
            if not page_token:
                break

        logger.info("Found %d message IDs, fetching bodies via batch API…", len(msg_ids))

        # Fetch full messages using Gmail batch API (up to 50 per HTTP call)
        import time

        messages: list[RawEmail] = []
        failed_ids: list[str] = []
        BATCH_SIZE = 50

        def _run_batch(ids: list[str]) -> list[str]:
            """Execute a batch fetch, return IDs that failed with 429."""
            batch = self.service.new_batch_http_request()
            retry_ids: list[str] = []

            def _make_callback(mid: str):
                def _cb(request_id: str, response: dict, exception: Exception | None) -> None:
                    if exception is not None:
                        if "429" in str(exception) or "rateLimitExceeded" in str(exception):
                            retry_ids.append(mid)
                        else:
                            logger.warning("Failed to fetch message %s: %s", mid, exception)
                        return
                    raw = self._parse_message(mid, response)
                    if raw is not None:
                        messages.append(raw)
                return _cb

            for mid in ids:
                batch.add(
                    self.service.users().messages().get(userId="me", id=mid, format="full"),
                    callback=_make_callback(mid),
                )
            batch.execute()
            return retry_ids

        # First pass: fetch all in batches of BATCH_SIZE
        all_retry_ids: list[str] = []
        for batch_start in range(0, len(msg_ids), BATCH_SIZE):
            batch_ids = msg_ids[batch_start : batch_start + BATCH_SIZE]
            retries = _run_batch(batch_ids)
            all_retry_ids.extend(retries)
            logger.debug(
                "Batch fetched %d/%d (%d rate-limited)",
                min(batch_start + BATCH_SIZE, len(msg_ids)),
                len(msg_ids),
                len(retries),
            )
            if batch_start + BATCH_SIZE < len(msg_ids):
                time.sleep(1)

        # Retry pass: re-fetch any 429'd messages with smaller batches and longer delays
        if all_retry_ids:
            logger.info("Retrying %d rate-limited messages…", len(all_retry_ids))
            time.sleep(3)
            RETRY_BATCH = 25
            for batch_start in range(0, len(all_retry_ids), RETRY_BATCH):
                batch_ids = all_retry_ids[batch_start : batch_start + RETRY_BATCH]
                still_failed = _run_batch(batch_ids)
                if still_failed:
                    logger.warning(
                        "%d messages still failed after retry", len(still_failed)
                    )
                if batch_start + RETRY_BATCH < len(all_retry_ids):
                    time.sleep(2)

        logger.info("Fetched %d messages", len(messages))
        return messages

    def _parse_message(self, msg_id: str, msg: dict) -> RawEmail | None:
        """Parse a full message response into a RawEmail."""
        try:
            headers = {h["name"].lower(): h["value"] for h in msg["payload"]["headers"]}
        except (KeyError, TypeError):
            logger.warning("Malformed message %s, skipping", msg_id)
            return None
        subject = headers.get("subject", "(no subject)")
        sender = headers.get("from", "unknown")
        date = headers.get("date", "")
        snippet = msg.get("snippet", "")

        body_html, body_text = self._extract_body(msg["payload"])

        return RawEmail(
            id=msg_id,
            subject=subject,
            sender=sender,
            date=date,
            snippet=snippet,
            body_html=body_html,
            body_text=body_text,
        )

    @staticmethod
    def _extract_body(payload: dict) -> tuple[str, str]:
        """Recursively extract HTML and plain-text bodies from a message payload."""
        html_parts: list[str] = []
        text_parts: list[str] = []

        def _walk(part: dict) -> None:
            mime = part.get("mimeType", "")
            if mime == "text/html":
                data = part.get("body", {}).get("data", "")
                if data:
                    html_parts.append(
                        base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                    )
            elif mime == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    text_parts.append(
                        base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                    )
            for sub in part.get("parts", []):
                _walk(sub)

        _walk(payload)
        return "\n".join(html_parts), "\n".join(text_parts)

    # ── Send ─────────────────────────────────────────────────────────────

    def send_briefing(self, html_body: str, subject: str) -> None:
        """Send the briefing email to the user's own inbox (or configured recipient)."""
        recipient = self._settings.recipient_email
        if not recipient:
            profile = self.service.users().getProfile(userId="me").execute()
            recipient = profile["emailAddress"]

        msg = MIMEMultipart("alternative")
        msg["To"] = recipient
        msg["Subject"] = subject
        msg.attach(MIMEText(html_body, "html"))

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        self.service.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()

        logger.info("Briefing sent to %s", recipient)

    # ── Label management ────────────────────────────────────────────────

    def ensure_label(self, label_name: str) -> str:
        """Find or create a Gmail label. Returns the label ID."""
        results = self.service.users().labels().list(userId="me").execute()
        for label in results.get("labels", []):
            if label["name"] == label_name:
                logger.debug("Found existing label '%s' (id=%s)", label_name, label["id"])
                return label["id"]

        # Create the label
        created = self.service.users().labels().create(
            userId="me",
            body={
                "name": label_name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            },
        ).execute()
        logger.info("Created Gmail label '%s' (id=%s)", label_name, created["id"])
        return created["id"]

    # ── Mark read ────────────────────────────────────────────────────────

    def mark_as_read(self, message_ids: list[str]) -> None:
        """Remove the UNREAD label from the given messages."""
        if not message_ids:
            return

        for i in range(0, len(message_ids), 1000):
            batch = message_ids[i : i + 1000]
            self.service.users().messages().batchModify(
                userId="me",
                body={"ids": batch, "removeLabelIds": ["UNREAD"]},
            ).execute()

        logger.info("Marked %d messages as read", len(message_ids))

    def move_to_label(self, message_ids: list[str], label_id: str) -> None:
        """Add a label to messages (keeps them unread)."""
        if not message_ids:
            return

        for i in range(0, len(message_ids), 1000):
            batch = message_ids[i : i + 1000]
            self.service.users().messages().batchModify(
                userId="me",
                body={"ids": batch, "addLabelIds": [label_id]},
            ).execute()

        logger.info("Moved %d messages to label %s", len(message_ids), label_id)
