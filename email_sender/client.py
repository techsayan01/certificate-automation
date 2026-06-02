"""
EmailClient — sends emails via Gmail API using proper MIME structure:

  multipart/mixed
    multipart/related          ← HTML body + CID inline images (e.g. confetti header)
      text/html
      image/png  (Content-ID: <confetti_header>)
    application/pdf            ← certificate attachment
    image/png                  ← laurel / extra attachments

CID images are referenced in HTML as  <img src="cid:confetti_header">
and travel inside the email — no external hosting, no base64 data URIs.
"""

import base64
import json
import os
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
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
        '<div style="font-family:Arial,sans-serif;font-size:13px;'
        'color:#555;margin-top:8px">' + signature_html + "</div>"
    )
    if _SIG_MARKER in html_body:
        return html_body.replace(_SIG_MARKER, wrapped)
    if "</body>" in html_body:
        return html_body.replace("</body>", wrapped + "\n</body>")
    return html_body + wrapped


def _load_inline_images() -> dict[str, str]:
    """
    Load inline_images.json from the project's templates folder.
    Returns {cid_name: absolute_file_path}.
    """
    map_path = Path(Config.PROJECT_TEMPLATE_DIR) / "inline_images.json"
    if not map_path.exists():
        return {}
    with open(map_path) as fh:
        raw = json.load(fh)          # { "confetti_header": "assets/confetti_header.png" }
    project_root = Path(f"projects/{Config.PROJECT}")
    return {
        cid: str(project_root / rel_path)
        for cid, rel_path in raw.items()
    }


def _mime_file(file_path: str) -> MIMEApplication:
    """Regular (downloadable) attachment for any file type."""
    with open(file_path, "rb") as fh:
        data = fh.read()
    ext  = Path(file_path).suffix.lower()
    sub  = {"pdf": "pdf", ".png": "png", ".jpg": "jpeg"}.get(ext, "octet-stream")
    part = MIMEApplication(data, _subtype=sub)
    part.add_header("Content-Disposition", "attachment",
                    filename=Path(file_path).name)
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
        final_body    = _inject_signature(html_body, self._signature)
        inline_images = _load_inline_images()   # {cid: path}

        # ── root: multipart/mixed ────────────────────────────────────────────
        root = MIMEMultipart("mixed")
        root["To"]      = f"{to_name} <{to_email}>"
        root["From"]    = f"{Config.EMAIL_FROM_NAME} <me>"
        root["Subject"] = subject

        # ── inner: multipart/related (HTML + CID inline images) ─────────────
        related = MIMEMultipart("related")

        html_part = MIMEText(final_body, "html", "utf-8")
        related.attach(html_part)

        for cid, img_path in inline_images.items():
            if not os.path.exists(img_path):
                logger.warning(f"Inline image not found, skipping: {img_path}")
                continue
            with open(img_path, "rb") as fh:
                img_data = fh.read()
            img_part = MIMEImage(img_data)
            img_part.add_header("Content-ID", f"<{cid}>")
            img_part.add_header("Content-Disposition", "inline",
                                filename=Path(img_path).name)
            related.attach(img_part)
            logger.debug(f"  Inline image attached: cid:{cid} → {img_path}")

        root.attach(related)

        # ── certificate PDF ──────────────────────────────────────────────────
        if not os.path.exists(attachment_path):
            raise FileNotFoundError(f"Certificate PDF not found: {attachment_path}")
        root.attach(_mime_file(attachment_path))

        # ── extra attachments (additional certs, laurel PNG, etc.) ──────────
        for extra in (extra_attachments or []):
            # Accept both absolute/CWD-relative paths (cert PDFs) and
            # project-relative paths (asset files like laurels)
            direct = Path(extra)
            project_path = Path(f"projects/{Config.PROJECT}") / extra
            if direct.exists():
                full_path = direct
            elif project_path.exists():
                full_path = project_path
            else:
                logger.warning(f"  Extra attachment not found: {extra}")
                continue
            root.attach(_mime_file(str(full_path)))
            logger.debug(f"  Extra attachment: {full_path.name}")

        # ── send ─────────────────────────────────────────────────────────────
        raw = base64.urlsafe_b64encode(root.as_bytes()).decode()
        try:
            self._service.users().messages().send(
                userId="me", body={"raw": raw}
            ).execute()
        except HttpError as exc:
            raise RuntimeError(
                f"Gmail API error sending to {to_email}: {exc}"
            ) from exc
