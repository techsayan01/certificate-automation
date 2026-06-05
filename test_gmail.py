"""
Gmail test tool — sends either a plain connectivity check or a full
template preview with real attachments.

Usage:
    # Plain connectivity check (no template)
    python test_gmail.py --project GVFF --to techsayan01@gmail.com

    # Full template preview with dummy PDF
    python test_gmail.py --project GVFF --to techsayan01@gmail.com --preview

    # Full template preview with a REAL Canva-generated certificate
    python test_gmail.py --project GVFF --to techsayan01@gmail.com --preview --canva \
        --name "Renato Santana" --film "Hunting Fireflies" --category "Award Winner"
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


# ── tracking-number test ───────────────────────────────────────────────────────

def send_by_tracking(
    project: str,
    to_address: str,
    tracking_number: str,
    csv_path: str,
    season: str | None,
    season_date: str | None,
    use_canva: bool,
):
    """Find a row by tracking number in the CSV and send a real test email."""
    Config.load(project)
    Config.validate()
    if season:      Config.CERT_SEASON_TEXT      = season
    if season_date: Config.CERT_SEASON_DATE_TEXT = season_date

    from csv_reader.reader import CSVReader
    csv_file = csv_path or Config.CSV_PATH
    all_rows = CSVReader(csv_file).load()

    # Find by tracking number (column: Tracking Number)
    recipient = next(
        (r for r in all_rows
         if r["raw"].get("Tracking Number", "").strip().upper() == tracking_number.upper()),
        None,
    )
    if not recipient:
        print(f"\n[ERROR] Tracking number '{tracking_number}' not found in {csv_file}")
        return

    name         = recipient["name"]
    project_name = recipient["project"]
    email_cat    = recipient["email_template_status"]
    certificates = recipient["certificates"]

    print(f"\nTracking : {tracking_number}")
    print(f"Name     : {name}")
    print(f"Project  : {project_name}")
    print(f"Email    : {to_address}  (redirected from {recipient['email']})")
    print(f"Certs    : {len(certificates)}")
    for c in certificates:
        print(f"           [{c['status']}] {c['category']}")
    print(f"Template : {email_cat}")

    output_dir = Path(Config.OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)

    cert_paths: list[str] = []

    if use_canva:
        from canva.design import CanvaDesignManager
        canva = CanvaDesignManager()
        for cert in certificates:
            cat    = cert["category"]
            status = cert["status"]
            slug   = cat.replace(" ", "_").replace("/", "_")[:40]
            pdf    = output_dir / f"test_{tracking_number}_{slug}.pdf"
            print(f"\nGenerating [{status}] certificate: {cat}")
            canva.generate_certificate(name, project_name, cat, str(pdf))
            cert_paths.append(str(pdf))
    else:
        # Dummy placeholder PDFs (one per cert so attachment count is right)
        for i, cert in enumerate(certificates):
            dummy = output_dir / f"test_{tracking_number}_dummy_{i+1}.pdf"
            if not dummy.exists():
                dummy.write_bytes(
                    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj "
                    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj "
                    b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj "
                    b"xref\n0 4\ntrailer<</Root 1 0 R/Size 4>>\nstartxref\n9\n%%EOF"
                )
            cert_paths.append(str(dummy))

    template_manager = TemplateManager()
    html_body = template_manager.render(
        category=email_cat,
        context={**recipient, "category": email_cat},
    )

    attachment_manager = AttachmentManager()
    extras = cert_paths[1:] + attachment_manager.get(email_cat)

    client = EmailClient()
    client.send(
        to_email=to_address,
        to_name=name,
        subject=f"[TEST] {Config.EMAIL_SUBJECT}",
        html_body=html_body,
        attachment_path=cert_paths[0],
        extra_attachments=extras,
    )

    print(f"\n✅ Test email sent to {to_address}")
    print(f"   Certificates : {len(cert_paths)} PDFs attached")
    print(f"   Laurel       : {attachment_manager.get(email_cat) or 'none'}")
    print(f"   Template     : {email_cat}")


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

def send_preview(
    project: str,
    to_address: str,
    category: str,
    name: str,
    film: str,
    use_canva: bool = False,
):
    Config.load(project)
    Config.validate()

    print(f"Project  : {project}")
    print(f"Category : {category}")
    print(f"Name     : {name}")
    print(f"Film     : {film}")

    # Render the HTML template
    print("Rendering email template…")
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

    # Certificate PDF — real Canva generation or dummy placeholder
    cert_path = Path("data/output/preview_certificate.pdf")
    cert_path.parent.mkdir(parents=True, exist_ok=True)

    if use_canva:
        from canva.design import CanvaDesignManager
        print("Generating certificate via Canva API…")
        CanvaDesignManager().generate_certificate(
            name=name,
            project=film,
            category=category,
            output_path=str(cert_path),
        )
        print(f"Certificate saved → {cert_path}")
    else:
        if not cert_path.exists():
            cert_path.write_bytes(
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
        attachment_path=str(cert_path),
        extra_attachments=extra,
    )

    print(f"\n✅ Preview sent to {to_address}")
    print(f"   Template    : {category}")
    print(f"   Certificate : {'Canva-generated' if use_canva else 'dummy placeholder'}")
    print(f"   Extras      : {extra if extra else 'none'}")
    print("\nCheck your inbox — the email shows exactly what recipients will receive.")


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project",  required=True,  help="Project name")
    parser.add_argument("--to",       required=True,  help="Recipient email")
    parser.add_argument("--preview",  action="store_true",
                        help="Send a full template preview instead of a plain test")
    parser.add_argument("--canva",       action="store_true",
                        help="Generate real certificates via Pillow")
    parser.add_argument("--category",    default="Award Winner",
                        help="Category for --preview (default: Award Winner)")
    parser.add_argument("--name",        default="Renato Santana",
                        help="Sample name for --preview")
    parser.add_argument("--film",        default="Hunting Fireflies",
                        help="Sample film for --preview")
    parser.add_argument("--season",      default=None,
                        help="Season label, e.g. 'Season 5'")
    parser.add_argument("--season-date", default=None,
                        help="Season date range, e.g. 'Sep - Jan 2026'")
    parser.add_argument("--tracking",    default=None,
                        help="Test with a real CSV row, e.g. GV1450")
    parser.add_argument("--csv",         default=None,
                        help="Path to CSV file (overrides CSV_PATH in .env)")
    args = parser.parse_args()

    if args.tracking:
        send_by_tracking(
            project=args.project,
            to_address=args.to,
            tracking_number=args.tracking,
            csv_path=args.csv,
            season=args.season,
            season_date=args.season_date,
            use_canva=args.canva,
        )
    elif args.preview:
        if args.season or args.season_date:
            from config import Config as _C
            _C.load(args.project)
            if args.season:      _C.CERT_SEASON_TEXT      = args.season
            if args.season_date: _C.CERT_SEASON_DATE_TEXT = args.season_date
        send_preview(
            args.project, args.to, args.category,
            args.name, args.film,
            use_canva=args.canva,
        )
    else:
        send_plain_test(args.project, args.to)
