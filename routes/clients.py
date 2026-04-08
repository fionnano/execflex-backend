"""
Client outreach management (admin).

Three admin endpoints:

  POST /admin/clients/send-outreach   @require_admin
    Body: {
      contact_ids:    [uuid, ...]  OR  {all_not_contacted: true},
      template:       "intro" | "follow_up" | "demo_invite",
      custom_message: str (optional — appended as PS),
    }
    Renders the template, actually dispatches the email via
    modules.email_sender.send_client_outreach_email, marks the
    client_contacts row as contacted, and appends a timestamped
    entry to source_metadata.outreach_log.

  GET /admin/clients                  @require_admin
    Paginated list of client_contacts with optional filters:
    outreach_status, company, source, limit (max 200), offset.
    Ordered by created_at DESC.

  GET /admin/clients/stats            @require_admin
    Quick counts for the Outreach Stats tab — total, not_contacted,
    contacted, responded, not_interested.
"""
from datetime import datetime, timezone
from typing import Optional

from flask import Blueprint, request

from utils.auth_helpers import require_admin
from utils.response_helpers import ok, bad
from config.clients import supabase_client


clients_bp = Blueprint("clients", __name__)


# ── Email templates ──────────────────────────────────────────────────────────
#
# Templates use .replace() substitution rather than .format() so curly
# braces in marketing copy don't blow up. Two placeholders:
#   {first_name}  — recipient's first name (fallback "there")
#   {company}     — recipient's company    (fallback "your team")

_TEMPLATES: dict = {
    "intro": {
        "subject": "AI recruitment that finds your next hire in 48 hours",
        "body": (
            "Hi {first_name},\n"
            "\n"
            "I'm Fionnán — I run ExecFlex, Ireland's first AI executive search platform.\n"
            "\n"
            "We source, screen and shortlist senior candidates in 48 hours using AI. Every "
            "candidate goes through an identical structured interview — bias-free, EU AI Act "
            "compliant, with a full audit trail.\n"
            "\n"
            "If you're hiring at senior level this year, I'd love to show you how it works.\n"
            "\n"
            "Book a 20-minute demo: https://calendly.com/fionnano/30min\n"
            "\n"
            "Fionnán\n"
            "ExecFlex / ai·nm search\n"
        ),
    },
    "follow_up": {
        "subject": "Re: AI recruitment",
        "body": (
            "Hi {first_name},\n"
            "\n"
            "Just following up on my last note.\n"
            "\n"
            "We just completed a CFO search for an Irish healthtech company — sourced 20 "
            "candidates, Aidan screened them overnight, shortlist of 3 delivered by morning. "
            "Client had interviews booked within 48 hours.\n"
            "\n"
            "Worth 20 minutes to see if this could work for {company}?\n"
            "\n"
            "Book a call: https://calendly.com/fionnano/30min\n"
            "\n"
            "Fionnán\n"
        ),
    },
    "demo_invite": {
        "subject": "Live demo: AI executive search in action",
        "body": (
            "Hi {first_name},\n"
            "\n"
            "I'm running a short live demo of ExecFlex next week — 20 minutes to show exactly "
            "how AI screening works and what a candidate shortlist looks like.\n"
            "\n"
            "Reserve your spot: https://calendly.com/fionnano/30min\n"
            "\n"
            "Fionnán\n"
        ),
    },
}

_ALLOWED_TEMPLATES = set(_TEMPLATES.keys())


def _first_name_of(contact: dict) -> str:
    name = (contact.get("name") or "").strip()
    if not name:
        return "there"
    return name.split()[0]


def _render_template(template_key: str, contact: dict, custom_message: Optional[str]) -> tuple:
    tpl = _TEMPLATES[template_key]
    first_name = _first_name_of(contact)
    company = (contact.get("company") or "").strip() or "your team"

    subject = tpl["subject"]
    body = tpl["body"].replace("{first_name}", first_name).replace("{company}", company)

    if custom_message:
        body = body.rstrip() + "\n\nP.S. " + custom_message.strip() + "\n"

    return subject, body


@clients_bp.route("/admin/clients/send-outreach", methods=["POST"])
@require_admin
def send_client_outreach():
    if not supabase_client:
        return bad("Database not available", 503)

    data = request.get_json(force=True, silent=True) or {}
    template = (data.get("template") or "").strip().lower()
    contact_ids = data.get("contact_ids")
    all_not_contacted = bool(data.get("all_not_contacted"))
    custom_message = (data.get("custom_message") or "").strip() or None

    if template not in _ALLOWED_TEMPLATES:
        return bad(
            f"template must be one of {sorted(_ALLOWED_TEMPLATES)}",
            400,
        )

    if not all_not_contacted:
        if not isinstance(contact_ids, list) or not contact_ids:
            return bad(
                "Provide either contact_ids (array) or all_not_contacted=true",
                400,
            )
        contact_ids = [cid for cid in contact_ids if isinstance(cid, str) and cid]
        if not contact_ids:
            return bad("contact_ids contains no valid ids", 400)

    now_iso = datetime.now(timezone.utc).isoformat()
    admin_user_id = request.environ.get("authenticated_user_id") or "unknown"

    try:
        query = (
            supabase_client.table("client_contacts")
            .select("id, name, email, company, outreach_status, source_metadata")
        )
        if all_not_contacted:
            query = query.eq("outreach_status", "not_contacted")
        else:
            query = query.in_("id", contact_ids)
        resp = query.limit(500).execute()
    except Exception as e:
        return bad(f"Failed to fetch contacts: {e}", 500)

    rows = resp.data or []

    errors: list = []
    if not all_not_contacted:
        found_ids = {r["id"] for r in rows}
        for cid in contact_ids:
            if cid not in found_ids:
                errors.append({"id": cid, "reason": "not_found"})

    sent = 0
    skipped = 0

    from modules.email_sender import send_client_outreach_email

    for row in rows:
        cid = row["id"]
        email = (row.get("email") or "").strip()
        if not email or "@" not in email:
            skipped += 1
            errors.append({"id": cid, "reason": "no_email"})
            continue

        subject, body = _render_template(template, row, custom_message)

        try:
            delivered = send_client_outreach_email(
                recipient_email=email,
                recipient_name=row.get("name"),
                subject=subject,
                body=body,
            )
        except Exception as e:
            print(f"[CLIENT-OUTREACH] send raised for {cid}: {e}", flush=True)
            delivered = False

        if not delivered:
            skipped += 1
            errors.append({"id": cid, "reason": "send_failed"})
            continue

        # Update outreach_status + log. Preserve any earlier log entries.
        try:
            existing_sm = row.get("source_metadata") or {}
            log = list(existing_sm.get("outreach_log") or [])
            log.append({
                "template": template,
                "sent_at": now_iso,
                "sent_by": admin_user_id,
            })
            new_sm = {
                **existing_sm,
                "outreach_log": log,
                "last_outreach_template": template,
                "last_outreach_date": now_iso,
            }
            supabase_client.table("client_contacts").update({
                "outreach_status": "contacted",
                "source_metadata": new_sm,
                "updated_at": now_iso,
                "last_contacted_at": now_iso,
            }).eq("id", cid).execute()
        except Exception as e:
            print(f"[CLIENT-OUTREACH] status update failed {cid}: {e}", flush=True)
            # Email already went out, so we don't count this as a full skip.

        sent += 1

    print(
        f"[CLIENT-OUTREACH] template={template} admin={admin_user_id} "
        f"sent={sent} skipped={skipped} total_considered={len(rows)} "
        f"all_not_contacted={all_not_contacted}",
        flush=True,
    )

    return ok({
        "sent": sent,
        "skipped": skipped,
        "errors": errors[:50],
        "errors_truncated": len(errors) > 50,
        "total_considered": len(rows),
        "template": template,
    }, status=200)


@clients_bp.route("/admin/clients", methods=["GET"])
@require_admin
def list_clients():
    """
    GET /admin/clients

    Query params:
      outreach_status  — filter by status
      company          — exact-match filter
      source           — filter by ingestion source
      limit            — default 100, max 200
      offset           — default 0

    Returns {clients: [...], total, limit, offset, filters}.
    """
    if not supabase_client:
        return bad("Database not available", 503)

    try:
        limit = min(int(request.args.get("limit", 100)), 200)
    except (TypeError, ValueError):
        limit = 100
    try:
        offset = max(int(request.args.get("offset", 0)), 0)
    except (TypeError, ValueError):
        offset = 0

    outreach_status = (request.args.get("outreach_status") or "").strip() or None
    company = (request.args.get("company") or "").strip() or None
    source = (request.args.get("source") or "").strip() or None

    try:
        query = (
            supabase_client.table("client_contacts")
            .select(
                "id, name, title, company, email, work_phone, mobile, "
                "source, outreach_status, notes, created_at, updated_at, "
                "source_metadata",
                count="exact",
            )
        )
        if outreach_status:
            query = query.eq("outreach_status", outreach_status)
        if company:
            query = query.eq("company", company)
        if source:
            query = query.eq("source", source)
        query = query.order("created_at", desc=True).range(offset, offset + limit - 1)
        resp = query.execute()
    except Exception as e:
        return bad(f"Failed to list clients: {e}", 500)

    rows = resp.data or []
    total = resp.count if resp.count is not None else len(rows)

    return ok({
        "clients": rows,
        "total": total,
        "limit": limit,
        "offset": offset,
        "filters": {
            "outreach_status": outreach_status,
            "company": company,
            "source": source,
        },
    }, status=200)


@clients_bp.route("/admin/clients/stats", methods=["GET"])
@require_admin
def client_stats():
    if not supabase_client:
        return bad("Database not available", 503)

    def _count(filters: dict) -> int:
        try:
            q = supabase_client.table("client_contacts").select("id", count="exact")
            for k, v in filters.items():
                q = q.eq(k, v)
            return q.limit(1).execute().count or 0
        except Exception as e:
            print(f"[CLIENT-STATS] count failed {filters}: {e}", flush=True)
            return 0

    total = _count({})
    not_contacted = _count({"outreach_status": "not_contacted"})
    # Rows created before the status column was populated will have
    # NULL which Supabase won't match against .eq(). Compute the
    # "null or not_contacted" bucket by subtraction so the numbers
    # add up.
    contacted = _count({"outreach_status": "contacted"})
    responded = _count({"outreach_status": "responded"})
    not_interested = _count({"outreach_status": "not_interested"})
    null_status = max(total - not_contacted - contacted - responded - not_interested, 0)

    return ok({
        "total": total,
        "not_contacted": not_contacted + null_status,
        "contacted": contacted,
        "responded": responded,
        "not_interested": not_interested,
    }, status=200)
