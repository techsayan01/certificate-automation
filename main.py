"""
Certificate Automation Pipeline — entry point.

Usage:
  python main.py --project filmfreeway                   # process all rows
  python main.py --project filmfreeway --dry-run         # generate PDFs only, skip email
  python main.py --project filmfreeway --email a@b.com   # single recipient
  python main.py --project filmfreeway --csv path/to.csv # override CSV path
  python main.py --project filmfreeway --filter-status Finalist

Each project has its own Gmail credentials and settings in projects/<name>/.env.
Canva credentials are shared from the root .env.
"""

import argparse
import sys
import time
from pathlib import Path

from config import Config
from csv_reader.reader import CSVReader
from canva.design import CanvaDesignManager
from email_sender.client import EmailClient
from email_sender.template_manager import TemplateManager
from email_sender.attachment_manager import AttachmentManager
from utils.logger import get_logger

logger = get_logger("pipeline")


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate & email personalised certificates from a CSV."
    )
    parser.add_argument(
        "--project", type=str, required=True,
        help="Project name — must match a folder under projects/<name>/.env",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Generate PDFs only — do not send emails.",
    )
    parser.add_argument(
        "--email", type=str, default=None,
        help="Process only this recipient email address.",
    )
    parser.add_argument(
        "--csv", type=str, default=None,
        help="Path to the CSV file (overrides CSV_PATH in project .env).",
    )
    parser.add_argument(
        "--filter-status", type=str, default=None,
        help="Only process rows where 'Submission Status' matches this value.",
    )
    parser.add_argument(
        "--season", type=str, default=None,
        help="Season label printed on the certificate, e.g. 'Season 5'.",
    )
    parser.add_argument(
        "--season-date", type=str, default=None,
        help="Season date range printed on the certificate, e.g. 'Sep - Jan 2026'.",
    )
    return parser.parse_args()


# ── helpers ───────────────────────────────────────────────────────────────────

def _safe_filename(name: str, idx: int, category: str = "") -> str:
    """'Alice Johnson', 1, 'Best Short Film' → 'Alice_Johnson_1_Best_Short_Film.pdf'"""
    def safe(s):
        return "".join(c if c.isalnum() or c in " _-" else "_" for c in s).replace(" ", "_").strip("_")
    cat_slug = f"_{safe(category)}" if category else ""
    return f"{safe(name)}_{idx}{cat_slug}.pdf"


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()

    # ── load project config (root .env + projects/<name>/.env) ───────────────
    try:
        Config.load(args.project)
        Config.validate()
    except (FileNotFoundError, ValueError) as exc:
        logger.error(str(exc))
        sys.exit(1)

    logger.info(f"Loaded config — {Config.summary()}")

    # CLI overrides for season (take precedence over .env)
    if args.season:
        Config.CERT_SEASON_TEXT = args.season
    if args.season_date:
        Config.CERT_SEASON_DATE_TEXT = args.season_date

    if not Config.CERT_SEASON_TEXT:
        logger.error("Season not set. Pass --season 'Season 5' or set CERT_SEASON_TEXT in .env")
        sys.exit(1)
    if not Config.CERT_SEASON_DATE_TEXT:
        logger.error("Season date not set. Pass --season-date 'Sep - Jan 2026' or set CERT_SEASON_DATE_TEXT in .env")
        sys.exit(1)

    logger.info(f"Season: {Config.CERT_SEASON_TEXT} | {Config.CERT_SEASON_DATE_TEXT}")

    dry_run = args.dry_run or Config.DRY_RUN
    csv_path = args.csv or Config.CSV_PATH
    output_dir = Path(Config.OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── load recipients ───────────────────────────────────────────────────────
    try:
        reader = CSVReader(csv_path)
        recipients = reader.load(
            filter_email=args.email,
            filter_status=args.filter_status,
        )
    except FileNotFoundError as exc:
        logger.error(str(exc))
        sys.exit(1)

    if not recipients:
        logger.warning("No recipients found — check your CSV and filters.")
        sys.exit(0)

    logger.info(
        f"{'[DRY RUN] ' if dry_run else ''}"
        f"Starting pipeline for {len(recipients)} recipient(s)…"
    )

    # ── setup clients ─────────────────────────────────────────────────────────
    canva = CanvaDesignManager()
    template_manager    = TemplateManager()
    attachment_manager  = AttachmentManager()
    email_client: EmailClient | None = None

    if not dry_run:
        email_client = EmailClient()

    # ── process ───────────────────────────────────────────────────────────────
    sent = skipped = failed = 0
    total = len(recipients)

    for idx, recipient in enumerate(recipients, start=1):
        name         = recipient["name"]
        email        = recipient["email"]
        project      = recipient["project"]
        certificates = recipient["certificates"]        # list of {category, status}
        email_cat    = recipient["email_template_status"]  # most prestigious

        logger.info(
            f"[{idx}/{total}] {name} <{email}> — "
            f"{len(certificates)} certificate(s) | email template: {email_cat}"
        )

        try:
            # ── 1. generate one certificate per category ──────────────────────
            cert_paths: list[str] = []
            for cert in certificates:
                cat    = cert["category"]
                status = cert["status"]
                pdf_path = output_dir / _safe_filename(name, idx, cat)

                if pdf_path.exists():
                    logger.info(f"  Reusing: {pdf_path.name}")
                else:
                    logger.info(f"  Generating [{status}] certificate: {cat}")
                    canva.generate_certificate(
                        name=name,
                        project=project,
                        category=cat,
                        output_path=str(pdf_path),
                    )
                    logger.info(f"  Saved: {pdf_path.name}")

                cert_paths.append(str(pdf_path))
                time.sleep(Config.CANVA_REQUEST_DELAY)

            # ── 2. send one email with all certificates ───────────────────────
            if dry_run:
                logger.info(f"  [DRY RUN] Would email {email} — {len(cert_paths)} PDFs")
                skipped += 1
            else:
                html_body = template_manager.render(
                    category=email_cat,
                    context={
                        **recipient,
                        "category":     email_cat,
                        "pdf_filename": cert_paths[0] if cert_paths else "",
                    },
                )
                # First cert = primary attachment; rest + laurels = extras
                extras = cert_paths[1:] + attachment_manager.get(email_cat)
                email_client.send(
                    to_email=email,
                    to_name=name,
                    subject=Config.EMAIL_SUBJECT,
                    html_body=html_body,
                    attachment_path=cert_paths[0],
                    extra_attachments=extras,
                )
                logger.info(f"  Email sent ✓  ({len(cert_paths)} certificate(s) attached)")
                sent += 1

        except Exception as exc:
            logger.error(f"  FAILED — {exc}")
            failed += 1

    # ── summary ───────────────────────────────────────────────────────────────
    print()
    print("=" * 52)
    print("  PIPELINE SUMMARY")
    print("=" * 52)
    print(f"  Total recipients : {total}")
    if dry_run:
        print(f"  PDFs generated   : {total - failed}")
        print(f"  Emails skipped   : {skipped}  (dry-run mode)")
    else:
        print(f"  Emails sent      : {sent}")
    print(f"  Failed           : {failed}")
    print("=" * 52)

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
