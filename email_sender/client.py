"""
EmailClient — builds a MIME multipart message and sends it via the Gmail API.

Every email includes:
  - Rendered HTML body (Jinja2 template)
  - Gmail account signature injected automatically
  - Certificate PDF as an attachment
  - Any extra attachments (e.g. laurel PNG) configured in attachments.json
"""

import base64
import os
from email.mime.application import MIMEApplication
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import Config
from email_sender.auth import get_gmail_credentials
from email_sender.signature import get_signature
from utils.logger import get_logger

logger = get_logger(__name__)

_SIG_MARKER = "<!-- GMAIL_SIGNATURE -->"


def _inject_signature(html_body: str, signature_html: str) -> str:
    if not signature_html:
        return html_body
    wrapped = (
        '<div style="font-family:Arial,sans-serif;font-size:13px;color:#555;margin-top:8px">'
        + signature_html
        + "</div>"
    )
    if _SIG_MARKER in html_body:
        return html_body.replace(_SIG_MARKER, wrapped)
    if "</body>" in html_body:
        return html_body.replace("</body>", wrapped + "\n</body>")
    return html_body + wrapped


def _mime_attachment(file_path: str) -> MIMEBase:
    """Build a MIME part for any file type based on its extension."""
    path = Path(file_path)
    ext  = path.suffix.lower()

    with open(file_path, "rb") as fh:
        data = fh.read()

    if ext == ".pdf":
        part = MIMEApplication(data, _subtype="pdf")
    elif ext in (".png", ".jpg", ".jpeg"):
        subtype = "jpeg" if ext in (".jpg", ".jpeg") else "png"
        part = MIMEApplication(data, _subtype=subtype)
    else:
        part = MIMEApplication(data, _subtype="octet-stream")

    part.add_header("Content-Disposition", "attachment", filename=path.name)
    return part


class EmailClient:
    def __init__(self):
        creds = get_gmail_credentials()
        self._service   = build("gmail", "v1", credentials=creds)
        self._signature = get_signature(Config.PROJECT)

    def send(
        self,
        to_email: str,
        to_name: str,
        subject: str,
        html_body: str,
        attachment_path: str,
        extra_attachments: list[str] | None = None,
    ) -> None:
        """
        Compose and send one email.

        Args:
            to_email:          Recipient email address.
            to_name:           Recipient display name.
            subject:           Email subject line.
            html_body:         Rendered HTML body.
            attachment_path:   Certificate PDF path (always attached).
            extra_attachments: Additional files to attach (e.g. laurel PNG).
        """
        final_body = _inject_signature(html_body, self._signature)

        msg = MIMEMultipart("mixed")
        msg["To"]      = f"{to_name} <{to_email}>"
        msg["From"]    = f"{Config.EMAIL_FROM_NAME} <me>"
        msg["Subject"] = subject

        # ── HTML body ─────────────────────────────────────────────────────────
        msg.attach(MIMEText(final_body, "html", "utf-8"))

        # ── Certificate PDF ───────────────────────────────────────────────────
        if not os.path.exists(attachment_path):
            raise FileNotFoundError(f"Certificate PDF not found: {attachment_path}")
        msg.attach(_mime_attachment(attachment_path))

        # ── Extra attachments (laurel, etc.) ──────────────────────────────────
        for extra in (extra_attachments or []):
            full_path = Path(f"projects/{Config.PROJECT}") / extra
            if full_path.exists():
                msg.attach(_mime_attachment(str(full_path)))
                logger.debug(f"  Attached extra file: {full_path.name}")
            else:
                logger.warning(f"  Extra attachment not found, skipping: {full_path}")

        # ── Send ──────────────────────────────────────────────────────────────
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        try:
            self._service.users().messages().send(
                userId="me", body={"raw": raw}
            ).execute()
        except HttpError as exc:
            raise RuntimeError(f"Gmail API error sending to {to_email}: {exc}") from exc
