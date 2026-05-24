"""
CSVReader — reads the FilmFreeway (or any compatible) CSV export and
returns a clean list of recipient dicts ready for the pipeline.

Each dict contains at minimum:
    name      – full name  (First Name + Last Name)
    email     – recipient email
    project   – project title
    category  – first submission category (cleaned)
    raw       – the original row dict for template access
"""

import csv
from typing import Optional
from config import Config
from utils.logger import get_logger

logger = get_logger(__name__)


def _clean_category(raw: str) -> str:
    """
    FilmFreeway may put multiple categories separated by commas.
    We take the first one and strip whitespace.
    """
    if not raw:
        return "General"
    first = raw.split(",")[0].strip()
    return first if first else "General"


class CSVReader:
    def __init__(self, csv_path: str):
        self.csv_path = csv_path

    def load(
        self,
        filter_email: Optional[str] = None,
        filter_status: Optional[str] = None,
    ) -> list[dict]:
        """
        Parse the CSV and return a list of recipient dicts.

        Args:
            filter_email:  If set, only return the row with this email address.
            filter_status: If set, only return rows where 'Submission Status'
                           matches this value (case-insensitive). Overrides the
                           CSV_FILTER_STATUS env variable.
        """
        status_filter = (
            filter_status
            or Config.CSV_FILTER_STATUS
            or None
        )

        recipients: list[dict] = []
        skipped = 0

        try:
            with open(self.csv_path, newline="", encoding="utf-8-sig") as fh:
                reader = csv.DictReader(fh)
                for row_num, row in enumerate(reader, start=2):  # row 1 = header
                    # ── required fields ───────────────────────────────────────
                    first = row.get(Config.CSV_FIRST_NAME_COL, "").strip()
                    last = row.get(Config.CSV_LAST_NAME_COL, "").strip()
                    email = row.get(Config.CSV_EMAIL_COL, "").strip()
                    project = row.get(Config.CSV_PROJECT_COL, "").strip()
                    category_raw = row.get(Config.CSV_CATEGORY_COL, "").strip()

                    full_name = f"{first} {last}".strip()

                    if not full_name or not email:
                        logger.warning(
                            f"Row {row_num}: missing name or email — skipping."
                        )
                        skipped += 1
                        continue

                    # ── optional status filter ────────────────────────────────
                    if status_filter:
                        row_status = row.get("Submission Status", "").strip()
                        if row_status.lower() != status_filter.lower():
                            skipped += 1
                            continue

                    # ── single-email filter ───────────────────────────────────
                    if filter_email and email.lower() != filter_email.lower():
                        continue

                    recipients.append({
                        "name": full_name,
                        "email": email,
                        "project": project or "—",
                        "category": _clean_category(category_raw),
                        "raw": dict(row),   # full row available in email templates
                    })

        except FileNotFoundError:
            raise FileNotFoundError(
                f"CSV not found: {self.csv_path}\n"
                "Set CSV_PATH in .env or pass --csv <path>."
            )

        logger.info(
            f"CSV loaded — {len(recipients)} recipient(s) to process, "
            f"{skipped} skipped."
        )
        return recipients
