"""
Gmail send client — Mongo-fed, refresh-token-based.

Each instance is bound to ONE festival's Gmail credentials:
    festival.gmail.client_id
    festival.gmail.client_secret_enc
    festival.gmail.refresh_token_enc
    festival.gmail.sender_email

Construction
────────────
    gmail = await GmailClient.for_festival(festival_id)
    await gmail.send_html(
        to_email, to_name,
        subject, html_body,
        attachments=[("Certificate.pdf", pdf_bytes, "application/pdf"), ...],
        inline_images={"gmail_signature": (b"...", "image/png")},
    )

MIME structure built (Gmail-safe)
─────────────────────────────────
    multipart/mixed
      multipart/related
        text/html              ← message body (Jinja-rendered)
        image/png (Content-ID) ← optional inline images
      application/pdf          ← certificate(s)
      image/png                ← laurel(s)

The fetched Gmail signature is appended to the body before sending; any
external <img src=> URLs inside it are rewritten to cid:gmail_signature
and the matching image is attached as a CID inline (so it renders even
when remote-image loading is blocked in the recipient's client).
"""

from __future__ import annotations

import base64
import re
from datetime import datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import httpx
from bson import ObjectId

from backend.app.db.client import MongoDB
from backend.app.services.crypto import decrypt, encrypt
from backend.app.settings import get_settings


GMAIL_TOKEN_URL    = "https://oauth2.googleapis.com/token"
GMAIL_SEND_URL     = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
GMAIL_SETTINGS_URL = "https://gmail.googleapis.com/gmail/v1/users/me/settings/sendAs/{sender}"

_SIG_MARKER  = "<!-- GMAIL_SIGNATURE -->"
_IMG_SRC_RE  = re.compile(r'(<img\b[^>]*?\bsrc=")https?://[^"]*(")', re.IGNORECASE)


class GmailAuthError(RuntimeError):
    pass


class GmailApiError(RuntimeError):
    pass


def _ext_to_mime_subtype(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return {
        ".pdf":  "pdf",
        ".png":  "png",
        ".jpg":  "jpeg",
        ".jpeg": "jpeg",
    }.get(ext, "octet-stream")


class GmailClient:
    def __init__(
        self,
        *,
        festival_id:    str,
        client_id:      str,
        client_secret:  str,
        refresh_token:  str,
        sender_email:   str,
        cached_signature_html: str = "",
    ):
        self._festival_id   = festival_id
        self._client_id     = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._sender_email  = sender_email
        self._signature_html = cached_signature_html

        self._access_token: str | None = None

    # ── Construction ─────────────────────────────────────────────────────────

    @classmethod
    async def for_festival(cls, festival_id: str) -> "GmailClient":
        if not ObjectId.is_valid(festival_id):
            raise GmailAuthError(f"Invalid festival_id {festival_id!r}")

        festival = await MongoDB.festivals().find_one({"_id": ObjectId(festival_id)})
        if not festival:
            raise GmailAuthError(f"Festival {festival_id} not found")

        gmail = festival.get("gmail") or {}
        if not (gmail.get("client_id") and gmail.get("client_secret_enc")
                and gmail.get("refresh_token_enc") and gmail.get("sender_email")):
            raise GmailAuthError(
                "Gmail is not fully connected for this festival. "
                "Check client_id, client_secret, refresh_token, and sender_email."
            )

        try:
            client_secret = decrypt(gmail["client_secret_enc"])
            refresh_token = decrypt(gmail["refresh_token_enc"])
        except ValueError as exc:
            raise GmailAuthError(
                f"Stored Gmail secrets can't be decrypted ({exc}). "
                "ENCRYPTION_KEY may have rotated — reconnect Gmail."
            )

        client = cls(
            festival_id=festival_id,
            client_id=gmail["client_id"],
            client_secret=client_secret,
            refresh_token=refresh_token,
            sender_email=gmail["sender_email"],
            cached_signature_html=gmail.get("signature_html", ""),
        )
        await client._refresh_access_token()
        await client._maybe_refresh_signature()
        return client

    # ── Token refresh ────────────────────────────────────────────────────────

    async def _refresh_access_token(self) -> None:
        async with httpx.AsyncClient(timeout=15) as http:
            r = await http.post(
                GMAIL_TOKEN_URL,
                data={
                    "grant_type":    "refresh_token",
                    "refresh_token": self._refresh_token,
                    "client_id":     self._client_id,
                    "client_secret": self._client_secret,
                },
            )
        if r.status_code != 200:
            raise GmailAuthError(
                f"Gmail token refresh failed {r.status_code}: {r.text[:200]}"
            )
        self._access_token = r.json()["access_token"]

    # ── Signature fetch ──────────────────────────────────────────────────────

    async def _maybe_refresh_signature(self) -> None:
        """Fetch the user's signature once per client instance.
        We cache it on the festival doc to spare the API call next run."""
        url = GMAIL_SETTINGS_URL.format(sender=self._sender_email)
        async with httpx.AsyncClient(timeout=15) as http:
            r = await http.get(
                url,
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
        if r.status_code != 200:
            # Non-fatal — emails just go without a signature.
            return

        sig = r.json().get("signature", "")
        if not sig:
            return
        # Rewrite external image srcs → CID inline ref (rendered from
        # the image we attach later).
        sig = _IMG_SRC_RE.sub(r'\1cid:gmail_signature\2', sig)

        if sig != self._signature_html:
            self._signature_html = sig
            await MongoDB.festivals().update_one(
                {"_id": ObjectId(self._festival_id)},
                {"$set": {
                    "gmail.signature_html": sig,
                    "updated_at": datetime.now(timezone.utc),
                }},
            )

    # ── Body assembly ────────────────────────────────────────────────────────

    def _inject_signature(self, html_body: str) -> str:
        if not self._signature_html:
            return html_body
        wrapped = (
            '<div style="font-family:Arial,sans-serif;font-size:13px;'
            'color:#555;margin-top:8px">' + self._signature_html + "</div>"
        )
        if _SIG_MARKER in html_body:
            return html_body.replace(_SIG_MARKER, wrapped)
        if "</body>" in html_body:
            return html_body.replace("</body>", wrapped + "\n</body>")
        return html_body + wrapped

    # ── Send ─────────────────────────────────────────────────────────────────

    async def send_html(
        self,
        *,
        to_email:     str,
        to_name:      str,
        subject:      str,
        html_body:    str,
        from_name:    str | None = None,
        attachments:  list[tuple[str, bytes, str]] | None = None,
        inline_images: dict[str, tuple[bytes, str]] | None = None,
    ) -> str:
        """
        Send an HTML email. Returns Gmail's message ID on success.

        attachments    : list of (filename, bytes, mime_type)
        inline_images  : { cid_name: (bytes, mime_type) } — referenced as
                         <img src="cid:NAME"> in the HTML body
        """
        body_with_sig = self._inject_signature(html_body)

        # multipart/related → HTML + inline images (cid:)
        related = MIMEMultipart("related")
        related.attach(MIMEText(body_with_sig, "html", "utf-8"))

        for cid, (data, mime_type) in (inline_images or {}).items():
            sub = mime_type.split("/")[-1] if mime_type else "png"
            img = MIMEImage(data, _subtype=sub)
            img.add_header("Content-ID", f"<{cid}>")
            img.add_header("Content-Disposition", "inline",
                           filename=f"{cid}.{sub}")
            related.attach(img)

        # multipart/mixed → related + downloadable attachments
        root = MIMEMultipart("mixed")
        sender_display = f"{from_name or ''} <{self._sender_email}>".strip()
        root["From"]    = sender_display if from_name else self._sender_email
        root["To"]      = f"{to_name} <{to_email}>"
        root["Subject"] = subject
        root.attach(related)

        for filename, data, mime_type in (attachments or []):
            sub = (mime_type.split("/")[-1] if mime_type
                   else _ext_to_mime_subtype(filename))
            part = MIMEApplication(data, _subtype=sub)
            part.add_header("Content-Disposition", "attachment", filename=filename)
            root.attach(part)

        raw = base64.urlsafe_b64encode(root.as_bytes()).decode()
        async with httpx.AsyncClient(timeout=60) as http:
            r = await http.post(
                GMAIL_SEND_URL,
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Content-Type":  "application/json",
                },
                json={"raw": raw},
            )

        # Gmail tokens occasionally expire mid-burst — refresh and retry once.
        if r.status_code == 401:
            await self._refresh_access_token()
            async with httpx.AsyncClient(timeout=60) as http:
                r = await http.post(
                    GMAIL_SEND_URL,
                    headers={
                        "Authorization": f"Bearer {self._access_token}",
                        "Content-Type":  "application/json",
                    },
                    json={"raw": raw},
                )

        if not r.is_success:
            raise GmailApiError(
                f"Gmail send failed {r.status_code}: {r.text[:300]}"
            )
        return r.json().get("id", "")
