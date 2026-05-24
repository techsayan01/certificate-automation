"""
Certificate Automation Pipeline — entry point.

Usage:
  python main.py                          # process all rows in CSV_PATH
  python main.py --dry-run                # generate PDFs only, skip email
  python main.py --email alice@ex.com     # single recipient
  python main.py --csv path/to/file.csv   # override CSV path
  python main.py --filter-status Finalist # only rows with that submission status
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
from utils.logger import get_logger

logger = get_logger("pipeline")


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate & email personalised certificates from a CSV."
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
        help="Path to the CSV file (overrides CSV_PATH in .env).",
    )
    parser.add_argument(
        "--filter-status", type=str, default=None,
        help="Only process rows where 'Submission Status' matches this value.",
    )
    return parser.parse_args()


# ── helpers ───────────────────────────────────────────────────────────────────

def _safe_filename(name: str, idx: int) -> str:
    """'Alice Johnson' → 'Alice_Johnson_1_certificate.pdf'"""
    safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in name)
    safe = safe.replace(" ", "_").strip("_")
    return f"{safe}_{idx}_certificate.pdf"


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()

    # ── validate config ───────────────────────────────────────────────────────
    try:
        Config.validate()
    except ValueError as exc:
        logger.error(str(exc))
        sys.exit(1)

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
    template_manager = TemplateManager(Config.EMAIL_TEMPLATE_DIR)
    email_client: EmailClient | None = None

    if not dry_run:
        email_client = EmailClient()

    # ── process ───────────────────────────────────────────────────────────────
    sent = skipped = failed = 0
    total = len(recipients)

    for idx, recipient in enumerate(recipients, start=1):
        name     = recipient["name"]
        email    = recipient["email"]
        project  = recipient["project"]
        category = recipient["category"]

        logger.info(f"[{idx}/{total}] {name} <{email}> — {category}")

        pdf_path = output_dir / _safe_filename(name, idx)

        try:
            # ── 1. generate certificate ───────────────────────────────────────
            if pdf_path.exists():
                logger.info(f"  Certificate already exists, reusing: {pdf_path.name}")
            else:
                logger.info("  Generating certificate via Canva…")
                canva.generate_certificate(
                    name=name,
                    project=project,
                    category=category,
                    output_path=str(pdf_path),
                )
                logger.info(f"  Saved: {pdf_path.name}")

            # ── 2. send email ─────────────────────────────────────────────────
            if dry_run:
                logger.info(f"  [DRY RUN] Would email {email}")
                skipped += 1
            else:
                html_body = template_manager.render(
                    category=category,
                    context={**recipient, "pdf_filename": pdf_path.name},
                )
                email_client.send(
                    to_email=email,
                    to_name=name,
                    subject=Config.EMAIL_SUBJECT,
                    html_body=html_body,
                    attachment_path=str(pdf_path),
                )
                logger.info(f"  Email sent ✓")
                sent += 1

        except Exception as exc:
            logger.error(f"  FAILED — {exc}")
            failed += 1

        # Rate-limit Canva API calls between recipients
        if idx < total:
            time.sleep(Config.CANVA_REQUEST_DELAY)

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
