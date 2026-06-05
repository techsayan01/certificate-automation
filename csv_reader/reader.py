"""
CSVReader — reads a FilmFreeway CSV export and returns recipient dicts
ready for the certificate pipeline.

Key behaviours
──────────────
• Multiple categories per row → each category gets its own certificate.
• Per-category status is extracted from the Submission Notes when present
  (pattern: "Category keyword - Winner/Finalist"), otherwise the row-level
  Judging Status is used for every category.
• The email template is chosen from the MOST PRESTIGIOUS status across all
  categories for that person (Award Winner > Finalist > Semi-Finalist > …).
• One email is sent per person regardless of how many categories they have.
  All certificates travel as separate PDF attachments in that single email.

Recipient dict structure
────────────────────────
{
    "name":                  str,
    "email":                 str,
    "project":               str,
    "overall_status":        str,   # raw Judging Status column value
    "email_template_status": str,   # most prestigious → used for email template lookup
    "certificates": [
        {"category": str, "status": str},   # one entry per category
    ],
    "raw": dict,                    # full original CSV row for template variables
}
"""

from __future__ import annotations

import csv
import re
from typing import Optional

from config import Config
from utils.logger import get_logger

logger = get_logger(__name__)

# ── Status precedence (highest first) ─────────────────────────────────────────
_STATUS_RANK = [
    "Award Winner",
    "Finalist",
    "Semi-Finalist",
    "Semifinalist",
    "Quarter-Finalist",
    "Official Selection",
    "Nominee",
    "Honorable Mention",
]

# Keywords that appear in notes → canonical status
_NOTE_STATUS_MAP = {
    "award winner": "Award Winner",
    "winner":       "Award Winner",
    "finalist":     "Finalist",
    "semi-finalist": "Semi-Finalist",
    "semifinalist": "Semi-Finalist",
    "quarter-finalist": "Quarter-Finalist",
    "official selection": "Official Selection",
    "nominee":      "Nominee",
    "honorable mention": "Honorable Mention",
    "special mention": "Honorable Mention",
}


def _rank(status: str) -> int:
    """Lower number = more prestigious."""
    s = status.strip().lower()
    for i, r in enumerate(_STATUS_RANK):
        if r.lower() in s or s in r.lower():
            return i
    return len(_STATUS_RANK)


def _most_prestigious(statuses: list[str]) -> str:
    if not statuses:
        return "Award Winner"
    return min(statuses, key=_rank)


def _parse_categories(raw: str) -> list[str]:
    """Split comma-separated categories, strip whitespace, drop empty."""
    return [c.strip() for c in raw.split(",") if c.strip()]


def _parse_note_statuses(notes: str, categories: list[str]) -> dict[str, str]:
    """
    Scan the Submission Notes for lines like:
        Best Actor - Winner
        Best Short Film - Finalist
    and match them to the actual category names.

    Returns {category: status} for categories whose status was found in notes.
    Categories not mentioned fall back to the row-level Judging Status.
    """
    if not notes or not categories:
        return {}

    result: dict[str, str] = {}

    # Each "User:" block in FilmFreeway notes ends with the category-status line
    # Pattern: something - status_keyword
    for line in notes.splitlines():
        line = line.strip()
        if not line or line.startswith("User:") or line.startswith("Time:") \
                or line.startswith("Shared"):
            continue

        # Look for "phrase - status_keyword"
        m = re.match(r"^(.+?)\s*[-–]\s*(.+)$", line)
        if not m:
            continue

        note_phrase = m.group(1).strip().lower()
        note_status_raw = m.group(2).strip().lower()

        # Resolve status keyword
        matched_status = None
        for keyword, canonical in _NOTE_STATUS_MAP.items():
            if keyword in note_status_raw:
                matched_status = canonical
                break
        if not matched_status:
            continue

        # Match to best-fitting category (category whose name contains the phrase)
        best_cat = None
        best_overlap = 0
        for cat in categories:
            cat_lower = cat.lower()
            # Count words in common between note phrase and category name
            note_words = set(note_phrase.split())
            cat_words  = set(re.sub(r"[^a-z0-9 ]", " ", cat_lower).split())
            overlap = len(note_words & cat_words)
            if overlap > best_overlap:
                best_overlap = overlap
                best_cat = cat

        if best_cat and best_overlap >= 1 and best_cat not in result:
            result[best_cat] = matched_status
            logger.debug(f"  Note match: '{line}' → {best_cat} = {matched_status}")

    return result


class CSVReader:
    def __init__(self, csv_path: str):
        self.csv_path = csv_path

    def load(
        self,
        filter_email:  Optional[str] = None,
        filter_status: Optional[str] = None,
    ) -> list[dict]:
        """
        Parse the CSV and return a list of recipient dicts.
        Each dict contains a 'certificates' list (one entry per category).
        """
        status_filter = filter_status or Config.CSV_FILTER_STATUS or None

        recipients: list[dict] = []
        skipped = 0

        try:
            with open(self.csv_path, newline="", encoding="utf-8-sig") as fh:
                reader = csv.DictReader(fh)
                for row_num, row in enumerate(reader, start=2):

                    first    = row.get(Config.CSV_FIRST_NAME_COL, "").strip()
                    last     = row.get(Config.CSV_LAST_NAME_COL,  "").strip()
                    email    = row.get(Config.CSV_EMAIL_COL,       "").strip()
                    project  = row.get(Config.CSV_PROJECT_COL,     "").strip()
                    cat_raw  = row.get(Config.CSV_CATEGORY_COL,    "").strip()
                    notes    = row.get("Submission Notes",          "").strip()
                    judging  = row.get("Judging Status",            "").strip()

                    full_name = f"{first} {last}".strip()

                    if not full_name or not email:
                        logger.warning(f"Row {row_num}: missing name or email — skipping.")
                        skipped += 1
                        continue

                    # ── optional judging status filter ────────────────────────
                    if status_filter and judging.lower() != status_filter.lower():
                        skipped += 1
                        continue

                    # ── single-email filter ───────────────────────────────────
                    if filter_email and email.lower() != filter_email.lower():
                        continue

                    # ── expand categories ─────────────────────────────────────
                    categories = _parse_categories(cat_raw) or ["General"]

                    # ── per-category status from notes ────────────────────────
                    note_statuses = _parse_note_statuses(notes, categories)

                    certificates = []
                    for cat in categories:
                        # Notes override → else fall back to row-level Judging Status
                        status = note_statuses.get(cat) or judging or "Award Winner"
                        certificates.append({"category": cat, "status": status})

                    # ── email template = most prestigious status ───────────────
                    all_statuses = [c["status"] for c in certificates]
                    email_template_status = _most_prestigious(all_statuses)

                    recipients.append({
                        "name":                  full_name,
                        "email":                 email,
                        "project":               project or "—",
                        "overall_status":        judging,
                        "email_template_status": email_template_status,
                        # keep legacy "category" pointing at the most prestigious one
                        # so existing template code that uses recipient["category"] still works
                        "category": next(
                            (c["category"] for c in certificates
                             if c["status"] == email_template_status),
                            certificates[0]["category"],
                        ),
                        "certificates":          certificates,
                        "raw":                   dict(row),
                    })

        except FileNotFoundError:
            raise FileNotFoundError(
                f"CSV not found: {self.csv_path}\n"
                "Set CSV_PATH in .env or pass --csv <path>."
            )

        logger.info(
            f"CSV loaded — {len(recipients)} recipient(s), "
            f"{sum(len(r['certificates']) for r in recipients)} total certificates, "
            f"{skipped} rows skipped."
        )
        return recipients
