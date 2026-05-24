"""
Gmail test tool — sends either a plain connectivity check or a full
template preview with real attachments.

Usage:
    # Plain connectivity check (no template)
    python test_gmail.py --project cinema_na_santa --to techsayan01@gmail.com

    # Full template preview  (renders the template + attaches laurel)
    python test_gmail.py --project cinema_na_santa --to techsayan01@gmail.com \
        --preview --category "Award Winner" \
        --name "Renato Santana" --film "Hunting Fireflies"
"""

import argparse
import base64
import os
from email.mime.text import MIMEText
from pathlib import Path

from config import Config
from email_sender.auth import get_gmail_credentials
from email_sender.client import EmailClient
from email_sender.template_manager import TemplateManager
from email_sender.attachment_manager import AttachmentManager
from googleapiclient.discovery import build


# ── plain connectivity test ────────────────────────────────────────────────────

def send_plain_test(project: str, to_address: str):
    Config.load(project)
    Config.validate()

    print(f"Project : {project}")
    print(f"Gmail   : {Config.GMAIL_CLIENT_ID[:40]}…")
    print("Authorising…")

    creds   = get_gmail_credentials()
    service = build("gmail", "v1", credentials=creds)

    msg = MIMEText(
        f"Test email from Certificate Automation pipeline.\n\n"
        f"Project : {project}\n"
        f"Gmail integration is working correctly!",
        "plain"
    )
    msg["To"]      = to_address
    msg["From"]    = "me"
    msg["Subject"] = f"✅ Certificate Automation [{project}] — Gmail Test"

    raw    = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    result = service.users().messages().send(userId="me", body={"raw": raw}).execute()

    print(f"\n✅ Plain test email sent to {to_address}  (id: {result['id']})")


# ── full template preview ──────────────────────────────────────────────────────

def send_preview(project: str, to_address: str, category: str, name: str, film: str):
    Config.load(project)
    Config.validate()

    print(f"Project  : {project}")
    print(f"Category : {category}")
    print(f"Name     : {name}")
    print(f"Film     : {film}")
    print("Rendering template…")

    # Render the HTML template
    template_manager = TemplateManager()
    html_body = template_manager.render(
        category=category,
        context={
            "name":         name,
            "project":      film,
            "category":     category,
            "email":        to_address,
            "pdf_filename": "certificate_preview.pdf",
            "raw":          {},
        },
    )

    # Use a blank 1-page PDF placeholder (no Canva needed for preview)
    dummy_pdf = Path("data/output/preview_certificate.pdf")
    dummy_pdf.parent.mkdir(parents=True, exist_ok=True)
    if not dummy_pdf.exists():
        # Write a minimal valid PDF so the attachment isn't empty
        dummy_pdf.write_bytes(
            b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj "
            b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj "
            b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj "
            b"xref\n0 4\ntrailer<</Root 1 0 R/Size 4>>\nstartxref\n9\n%%EOF"
        )

    # Send via the full EmailClient (includes signature + extra attachments)
    extra = AttachmentManager().get(category)
    client = EmailClient()
    client.send(
        to_email=to_address,
        to_name=name,
        subject=f"[PREVIEW] {Config.EMAIL_SUBJECT}",
        html_body=html_body,
        attachment_path=str(dummy_pdf),
        extra_attachments=extra,
    )

    print(f"\n✅ Template preview sent to {to_address}")
    print(f"   Template : {category}")
    print(f"   Extras   : {extra if extra else 'none'}")
    print("\nCheck your inbox — the email shows exactly what recipients will receive.")


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project",  required=True,  help="Project name")
    parser.add_argument("--to",       required=True,  help="Recipient email")
    parser.add_argument("--preview",  action="store_true",
                        help="Send a full template preview instead of a plain test")
    parser.add_argument("--category", default="Award Winner",
                        help="Category to preview (default: Award Winner)")
    parser.add_argument("--name",     default="Renato Santana",
                        help="Sample recipient name")
    parser.add_argument("--film",     default="Hunting Fireflies",
                        help="Sample film/project name")
    args = parser.parse_args()

    if args.preview:
        send_preview(args.project, args.to, args.category, args.name, args.film)
    else:
        send_plain_test(args.project, args.to)
