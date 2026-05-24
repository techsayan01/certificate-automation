"""
EmailClient — builds a MIME multipart message and sends it via the Gmail API.

The message contains:
  - An HTML body (rendered from the Jinja2 template)
  - The certificate PDF as an attachment
"""

import base64
import mimetypes
import os
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import Config
from email_sender.auth import get_gmail_credentials
from utils.logger import get_logger

logger = get_logger(__name__)


class EmailClient:
    def __init__(self):
        creds = get_gmail_credentials()
        self._service = build("gmail", "v1", credentials=creds)

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
            html_body:       Rendered HTML body.
            attachment_path: Local path to the PDF file.
        """
        msg = MIMEMultipart("mixed")
        msg["To"] = f"{to_name} <{to_email}>"
        msg["From"] = (
            f"{Config.EMAIL_FROM_NAME} <me>"  # Gmail API sends from authenticated user
        )
        msg["Subject"] = subject

        # ── HTML body ─────────────────────────────────────────────────────────
        msg.attach(MIMEText(html_body, "html", "utf-8"))

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
            raise RuntimeError(f"Gmail API error sending to {to_email}: {exc}") from exc
