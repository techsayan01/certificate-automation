"""
CSV parsing — FilmFreeway export → recipient dicts ready for the pipeline.

Operates on bytes (an uploaded file) rather than a filesystem path.
Same logic as the CLI csv_reader/reader.py:
  • Each row's Submission Categories is split into per-category certificates.
  • Per-category status is extracted from Submission Notes when present
    ("Best Actor - Winner"), else falls back to the row-level Judging Status.
  • The "email template status" for the row is the most prestigious status
    across all certificates (Award Winner > Finalist > …).

Recipient dict shape
────────────────────
    {
      name, email, project,
      overall_status,                     # raw row-level status
      email_template_status,              # most prestigious
      category,                           # legacy: first cert's category
      certificates: [
        { category, status },             # one entry per category
      ],
      raw: {...},                         # full CSV row, for template vars
    }
"""

from __future__ import annotations

import csv
import io
import re
from typing import Iterable

# ── CSV column conventions (FilmFreeway defaults) ────────────────────────────
COL_FIRST_NAME = "First Name"
COL_LAST_NAME  = "Last Name"
COL_EMAIL      = "Email"
COL_PROJECT    = "Project Title"
COL_CATEGORY   = "Submission Categories"
COL_STATUS     = "Judging Status"
COL_NOTES      = "Submission Notes"

# ── Status ranking (lower = more prestigious) ────────────────────────────────
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

_NOTE_STATUS_MAP = {
    "award winner":      "Award Winner",
    "winner":            "Award Winner",
    "finalist":          "Finalist",
    "semi-finalist":     "Semi-Finalist",
    "semifinalist":      "Semi-Finalist",
    "quarter-finalist":  "Quarter-Finalist",
    "official selection":"Official Selection",
    "nominee":           "Nominee",
    "honorable mention": "Honorable Mention",
    "special mention":   "Honorable Mention",
}


def _rank(status: str) -> int:
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
    return [c.strip() for c in (raw or "").split(",") if c.strip()]


def _parse_note_statuses(notes: str, categories: list[str]) -> dict[str, str]:
    """Extract per-category statuses from FilmFreeway's notes block.

    Notes contain blocks like:
        User: Global Visionary
        Time: ...
        Shared with Submitter: No
        Best Actor - Winner

    We pull every "<phrase> - <status>" line and match the phrase against
    the best-fitting category in the row.
    """
    if not notes or not categories:
        return {}

    result: dict[str, str] = {}
    for raw_line in notes.splitlines():
        line = raw_line.strip()
        if (not line
                or line.startswith("User:")
                or line.startswith("Time:")
                or line.startswith("Shared")):
            continue

        m = re.match(r"^(.+?)\s*[-–]\s*(.+)$", line)
        if not m:
            continue
        phrase = m.group(1).strip().lower()
        status_raw = m.group(2).strip().lower()

        matched_status = next(
            (canonical for kw, canonical in _NOTE_STATUS_MAP.items() if kw in status_raw),
            None,
        )
        if not matched_status:
            continue

        # Map to the category whose name overlaps the phrase the most.
        phrase_words = set(phrase.split())
        best_cat, best_overlap = None, 0
        for cat in categories:
            cat_words = set(re.sub(r"[^a-z0-9 ]", " ", cat.lower()).split())
            overlap = len(phrase_words & cat_words)
            if overlap > best_overlap:
                best_overlap, best_cat = overlap, cat
        if best_cat and best_overlap >= 1 and best_cat not in result:
            result[best_cat] = matched_status

    return result


def parse_csv(csv_bytes: bytes) -> list[dict]:
    """Parse an uploaded CSV's bytes; return a list of recipient dicts."""
    text = csv_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))

    recipients: list[dict] = []
    for row in reader:
        first    = (row.get(COL_FIRST_NAME) or "").strip()
        last     = (row.get(COL_LAST_NAME)  or "").strip()
        email    = (row.get(COL_EMAIL)      or "").strip()
        project  = (row.get(COL_PROJECT)    or "").strip()
        cat_raw  = (row.get(COL_CATEGORY)   or "").strip()
        notes    = (row.get(COL_NOTES)      or "").strip()
        judging  = (row.get(COL_STATUS)     or "").strip()

        name = f"{first} {last}".strip()
        if not name or not email:
            continue       # row missing required fields — skip

        categories = _parse_categories(cat_raw) or ["General"]
        note_statuses = _parse_note_statuses(notes, categories)

        certificates = [
            {
                "category": cat,
                "status":   note_statuses.get(cat) or judging or "Award Winner",
            }
            for cat in categories
        ]
        all_statuses = [c["status"] for c in certificates]
        email_template_status = _most_prestigious(all_statuses)

        recipients.append({
            "name":                  name,
            "email":                 email,
            "project":               project or "—",
            "overall_status":        judging,
            "email_template_status": email_template_status,
            "category": next(
                (c["category"] for c in certificates
                 if c["status"] == email_template_status),
                certificates[0]["category"],
            ),
            "certificates":          certificates,
            "raw":                   dict(row),
        })

    return recipients
