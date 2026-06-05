"""
Pipeline orchestrator.

Given a Run document, this service:
  1. Loads the festival, the run's CSV bytes, and all certificate templates.
  2. Parses the CSV into recipient records.
  3. For each recipient:
       • Generates one Canva PDF per certificate they earned.
       • Selects the email template for the most-prestigious status they
         hold across all their categories.
       • Renders the email body with Jinja2.
       • Sends a single email with all PDFs + the matching laurel attached.
  4. Streams per-recipient progress back into runs[].log so the dashboard
     can poll and show what's happening.

Failure model
─────────────
  • Pre-flight failures (no Canva connection, malformed CSV, no templates)
    fail the entire run before any send happens.
  • Per-recipient failures are recorded in the log; the run continues so
    one bad row doesn't block the rest.
  • At the end, status flips to "done" if anything was sent and to "failed"
    only if NOTHING was sent.

CSV bytes location
──────────────────
  Phase 2/3 (local): csv_bytes is stored in the run doc itself
                     (binary field). Cloud Run instances are stateless so
                     we can't keep it on disk between request and worker.
  Phase 4 (later):   move CSV upload to GCS, reference via signed URL.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bson import ObjectId
from jinja2 import Environment, StrictUndefined

from backend.app.db.client import MongoDB
from backend.app.db.models import RunStatus
from backend.app.services.canva import CanvaApiError, CanvaAuthError, CanvaClient
from backend.app.services.csv_reader import parse_csv
from backend.app.services.gmail import GmailApiError, GmailAuthError, GmailClient


# ── Logging into the run doc ──────────────────────────────────────────────────

async def _log(run_id: ObjectId, level: str, msg: str) -> None:
    """Append a single log line to runs[].log and bump status timestamps."""
    await MongoDB.runs().update_one(
        {"_id": run_id},
        {"$push": {
            "log": {
                "ts":    datetime.now(timezone.utc),
                "level": level,
                "msg":   msg,
            }
        }},
    )


async def _set_status(run_id: ObjectId, status: RunStatus, **extra: Any) -> None:
    payload = {"status": status.value, **extra}
    await MongoDB.runs().update_one({"_id": run_id}, {"$set": payload})


async def _inc_totals(run_id: ObjectId, **inc: int) -> None:
    update = {f"totals.{k}": v for k, v in inc.items()}
    await MongoDB.runs().update_one({"_id": run_id}, {"$inc": update})


# ── Template resolution ──────────────────────────────────────────────────────

async def _load_templates(festival_id: str) -> dict[str, dict]:
    """Return {judging_status: template_doc}. One template per status."""
    out: dict[str, dict] = {}
    async for doc in MongoDB.cert_templates().find({"festival_id": festival_id}):
        status = doc.get("judging_status", "")
        if status:
            out[status] = doc
    return out


def _pick_template(templates_by_status: dict[str, dict], status: str) -> dict | None:
    """Return the template for the given status, or None if not configured."""
    return templates_by_status.get(status)


# ── Body rendering ───────────────────────────────────────────────────────────

_jinja_env = Environment(autoescape=False)


def _render_body(template_html: str, ctx: dict) -> str:
    """Render the email HTML body with the recipient's vars. Missing
    variables become empty strings rather than throwing — emails should
    still go even if a template references a future field."""
    try:
        return _jinja_env.from_string(template_html or "").render(**ctx)
    except Exception as exc:
        return f"<p>Template render error: {exc}</p>"


def _safe_filename(name: str, category: str) -> str:
    def safe(s: str) -> str:
        return "".join(c if c.isalnum() or c in " _-" else "_" for c in s) \
            .replace(" ", "_").strip("_")
    return f"{safe(name)}__{safe(category)}.pdf"


# ── Main entry ───────────────────────────────────────────────────────────────

async def execute_run(run_id: str) -> None:
    """Drive a single run from queued → done/failed.

    Safe to call from a background task in dev or a Cloud Tasks worker
    in production — does its own DB updates throughout so the dashboard
    can poll progress live."""
    if not ObjectId.is_valid(run_id):
        raise ValueError(f"Invalid run_id: {run_id!r}")
    run_oid = ObjectId(run_id)

    run = await MongoDB.runs().find_one({"_id": run_oid})
    if not run:
        raise ValueError(f"Run {run_id} not found")

    festival_id = run["festival_id"]

    await _set_status(
        run_oid,
        RunStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
    )
    await _log(run_oid, "info", f"Starting run for festival {festival_id}")

    # ── Pre-flight: festival + templates + CSV ───────────────────────────────
    festival = await MongoDB.festivals().find_one({"_id": ObjectId(festival_id)})
    if not festival:
        await _log(run_oid, "error", "Festival no longer exists")
        await _set_status(run_oid, RunStatus.FAILED,
                          finished_at=datetime.now(timezone.utc))
        return

    templates_by_status = await _load_templates(festival_id)
    if not templates_by_status:
        await _log(run_oid, "error",
                   "No certificate templates configured. "
                   "Add at least one in /festival/templates.")
        await _set_status(run_oid, RunStatus.FAILED,
                          finished_at=datetime.now(timezone.utc))
        return
    await _log(run_oid, "info",
               f"Configured statuses: {', '.join(sorted(templates_by_status.keys()))}")

    csv_bytes: bytes | None = run.get("csv_bytes")
    if not csv_bytes:
        await _log(run_oid, "error", "Run has no CSV attached")
        await _set_status(run_oid, RunStatus.FAILED,
                          finished_at=datetime.now(timezone.utc))
        return

    try:
        recipients = parse_csv(csv_bytes)
    except Exception as exc:
        await _log(run_oid, "error", f"CSV parse failed: {exc}")
        await _set_status(run_oid, RunStatus.FAILED,
                          finished_at=datetime.now(timezone.utc))
        return

    if not recipients:
        await _log(run_oid, "error", "CSV has no valid recipients")
        await _set_status(run_oid, RunStatus.FAILED,
                          finished_at=datetime.now(timezone.utc))
        return

    total = len(recipients)
    await _set_status(run_oid, RunStatus.RUNNING, **{"totals.total": total})
    await _log(run_oid, "info", f"Parsed {total} recipients")

    # ── Connect to Canva + Gmail ─────────────────────────────────────────────
    try:
        canva = await CanvaClient.for_festival(festival_id)
    except CanvaAuthError as exc:
        await _log(run_oid, "error", f"Canva connection failed: {exc}")
        await _set_status(run_oid, RunStatus.FAILED,
                          finished_at=datetime.now(timezone.utc))
        return

    try:
        gmail = await GmailClient.for_festival(festival_id)
    except GmailAuthError as exc:
        await _log(run_oid, "error", f"Gmail connection failed: {exc}")
        await _set_status(run_oid, RunStatus.FAILED,
                          finished_at=datetime.now(timezone.utc))
        return

    defaults    = festival.get("defaults") or {}
    subject     = defaults.get("email_subject", "Your certificate")
    from_name   = defaults.get("email_from_name", festival.get("name", ""))
    season      = run.get("season")      or defaults.get("season", "")
    season_date = run.get("season_date") or defaults.get("season_date", "")

    # ── Process recipients ───────────────────────────────────────────────────
    for idx, r in enumerate(recipients, start=1):
        await _log(run_oid, "info",
                   f"[{idx}/{total}] {r['name']} <{r['email']}> — "
                   f"{len(r['certificates'])} certificate(s)")

        try:
            attachments: list[tuple[str, bytes, str]] = []
            inline_images: dict[str, tuple[bytes, str]] = {}
            email_template_doc: dict | None = None
            laurel_path_for_email: str | None = None

            # Generate certs in order, keeping the most prestigious template
            # for the email body and laurel attachment.
            certs_skipped_for_row = 0
            for cert in r["certificates"]:
                tpl = _pick_template(templates_by_status, cert["status"])
                if not tpl:
                    await _log(
                        run_oid, "warn",
                        f"  No template for status '{cert['status']}' "
                        f"(category: {cert['category']}) — skipping this certificate",
                    )
                    await _inc_totals(run_oid, certs_skipped=1)
                    certs_skipped_for_row += 1
                    continue

                field_map = tpl.get("canva_field_map") or {}
                canva_data = {
                    field_map.get("name",        "Name"):       r["name"],
                    field_map.get("project",     "Project"):    r["project"],
                    field_map.get("category",    "Category"):   cert["category"],
                }
                if season and field_map.get("season"):
                    canva_data[field_map["season"]] = season
                if season_date and field_map.get("season_date"):
                    canva_data[field_map["season_date"]] = season_date

                design_id = await canva.autofill(
                    brand_template_id=tpl["canva_brand_template_id"],
                    title=f"Cert_{r['name'].replace(' ', '_')}_{cert['category'][:30]}",
                    data=canva_data,
                )
                url = await canva.export_pdf(design_id)
                pdf_bytes = await canva.download(url)

                filename = _safe_filename(r["name"], cert["category"])
                attachments.append((filename, pdf_bytes, "application/pdf"))

                # The template that wins the email body is the one matching
                # the row's email_template_status (most prestigious).
                if cert["status"] == r["email_template_status"] and email_template_doc is None:
                    email_template_doc = tpl
                    laurel_path_for_email = tpl.get("laurel_path") or ""

            if not attachments:
                await _log(
                    run_oid, "warn",
                    f"  No certificates produced for this recipient "
                    f"({certs_skipped_for_row} cert(s) skipped) — no email sent",
                )
                await _inc_totals(run_oid, skipped=1)
                continue

            # Fall back to the first available template if none matched the
            # row's most-prestigious status (e.g. only Finalist cert generated
            # for a row whose overall_status was Award Winner but the AW cert
            # got skipped because no template was configured).
            if email_template_doc is None:
                email_template_doc = next(iter(templates_by_status.values()))

            # Attach the laurel matching this row's status
            if laurel_path_for_email and Path(laurel_path_for_email).exists():
                laurel_bytes = Path(laurel_path_for_email).read_bytes()
                attachments.append(
                    (Path(laurel_path_for_email).name, laurel_bytes, "image/png")
                )

            body = _render_body(
                email_template_doc.get("email_template_html", ""),
                {
                    **r,
                    "season":      season,
                    "season_date": season_date,
                    "category":    r["email_template_status"],
                },
            )

            await gmail.send_html(
                to_email=r["email"],
                to_name=r["name"],
                subject=subject,
                html_body=body,
                from_name=from_name,
                attachments=attachments,
            )

            await _log(run_oid, "info",
                       f"  Email sent ✓  ({len(attachments)} attachment(s))")
            await _inc_totals(run_oid, sent=1)

        except (CanvaApiError, GmailApiError) as exc:
            await _log(run_oid, "error", f"  {type(exc).__name__}: {exc}")
            await _inc_totals(run_oid, failed=1)
        except Exception as exc:
            await _log(run_oid, "error", f"  Unexpected: {type(exc).__name__}: {exc}")
            await _inc_totals(run_oid, failed=1)

    # ── Finalise ─────────────────────────────────────────────────────────────
    final = await MongoDB.runs().find_one({"_id": run_oid}, projection={"totals": 1})
    totals = final.get("totals", {}) if final else {}
    sent = totals.get("sent", 0)

    final_status = RunStatus.DONE if sent > 0 else RunStatus.FAILED
    await _set_status(
        run_oid,
        final_status,
        finished_at=datetime.now(timezone.utc),
    )
    await _log(
        run_oid, "info",
        f"Run finished: sent={totals.get('sent',0)} "
        f"failed={totals.get('failed',0)} "
        f"skipped={totals.get('skipped',0)} "
        f"certs_skipped={totals.get('certs_skipped',0)}",
    )

    # Drop CSV bytes now that the run is done — they're recipient PII.
    await MongoDB.runs().update_one(
        {"_id": run_oid},
        {"$unset": {"csv_bytes": ""}},
    )
