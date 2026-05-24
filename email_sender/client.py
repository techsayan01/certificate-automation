"""
EmailClient — builds a MIME multipart message and sends it via the Gmail API.

The message contains:
  - An HTML body (rendered from the Jinja2 template)
  - The Gmail account signature appended automatically (fetched from Gmail Settings API)
  - The certificate PDF as an attachment
"""

import base64
import os
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import Config
from email_sender.auth import get_gmail_credentials
from email_sender.signature import get_signature
from utils.logger import get_logger

logger = get_logger(__name__)

# Marker in templates where the Gmail signature is injected.
# If the marker is absent the signature is appended just before </body>.
_SIG_MARKER = "<!-- GMAIL_SIGNATURE -->"


def _inject_signature(html_body: str, signature_html: str) -> str:
    """Insert the Gmail signature into the rendered HTML body."""
    if not signature_html:
        return html_body

    # Wrap signature to match Gmail's own rendering style
    wrapped = (
        '<div style="font-family:Arial,sans-serif;font-size:13px;'
        'color:#555;margin-top:8px">'
        + signature_html
        + "</div>"
    )

    if _SIG_MARKER in html_body:
        return html_body.replace(_SIG_MARKER, wrapped)

    # Fallback: inject just before closing </body>
    if "</body>" in html_body:
        return html_body.replace("</body>", wrapped + "\n</body>")

    return html_body + wrapped


class EmailClient:
    def __init__(self):
        creds = get_gmail_credentials()
        self._service = build("gmail", "v1", credentials=creds)
        # Fetch and cache the Gmail signature once at startup
        self._signature = get_signature(Config.PROJECT)

    def send(
        self,
        to_email: str,
        to_name: str,
        subject: str,
        html_body: str,
        attachment_path: str,
    ) -> None:
        """
        Compose and send one email with the certificate PDF attached.

        Args:
            to_email:        Recipient email address.
            to_name:         Recipient display name.
            subject:         Email subject line.
            html_body:       Rendered HTML body (from Jinja2 template).
            attachment_path: Local path to the PDF file.
        """
        # Inject Gmail account signature into the body
        final_body = _inject_signature(html_body, self._signature)

        msg = MIMEMultipart("mixed")
        msg["To"]      = f"{to_name} <{to_email}>"
        msg["From"]    = f"{Config.EMAIL_FROM_NAME} <me>"
        msg["Subject"] = subject

        # ── HTML body ─────────────────────────────────────────────────────────
        msg.attach(MIMEText(final_body, "html", "utf-8"))

        # ── PDF attachment ────────────────────────────────────────────────────
        if not os.path.exists(attachment_path):
            raise FileNotFoundError(
                f"Certificate PDF not found: {attachment_path}"
            )

        with open(attachment_path, "rb") as fh:
            pdf_data = fh.read()

        pdf_part = MIMEApplication(pdf_data, _subtype="pdf")
        pdf_part.add_header(
            "Content-Disposition",
            "attachment",
            filename=os.path.basename(attachment_path),
        )
        msg.attach(pdf_part)

        # ── send via Gmail API ────────────────────────────────────────────────
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        try:
            self._service.users().messages().send(
                userId="me",
                body={"raw": raw},
            ).execute()
        except HttpError as exc:
            raise RuntimeError(
                f"Gmail API error sending to {to_email}: {exc}"
            ) from exc
