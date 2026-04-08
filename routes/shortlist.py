"""
Shareable shortlists.

Three endpoints:

  POST /admin/roles/<opportunity_id>/create-shortlist  @require_auth
    Create a shortlist snapshot for this role and return a public URL.

  GET  /shortlist/<shortlist_id>                       (public, no auth)
    Public read for the shareable page. Returns role + candidate data.

  POST /shortlist/<shortlist_id>/request-intro         (public, no auth)
    Submit an introduction request from the public page — captured in
    shortlist_intro_requests for the employer to action from their
    dashboard.

Requires the tables in supabase/migrations/20260408_create_shortlists.sql.
"""
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify

from utils.auth_helpers import require_auth
from utils.response_helpers import ok, bad
from config.clients import supabase_client


shortlist_bp = Blueprint("shortlist", __name__)


# ── Admin: create a shortlist ───────────────────────────────────────────────

@shortlist_bp.route("/admin/roles/<opportunity_id>/create-shortlist", methods=["POST"])
@require_auth
def create_shortlist(opportunity_id: str):
    """
    POST /admin/roles/<opportunity_id>/create-shortlist

    Body (JSON):
      candidate_ids: [uuid, ...]  — match suggestion IDs or people_profiles IDs
      message:       str          — optional, shown on the public page

    Returns {shortlist_id, url, expires_at}.
    """
    if not supabase_client:
        return bad("Database not available", 503)

    data = request.get_json(force=True, silent=True) or {}
    candidate_ids = data.get("candidate_ids") or []
    message = (data.get("message") or "").strip() or None

    if not isinstance(candidate_ids, list) or not candidate_ids:
        return bad("candidate_ids must be a non-empty list", 400)
    # Limit to 5 per spec
    candidate_ids = candidate_ids[:5]

    user_id = request.environ.get("authenticated_user_id")
    if not user_id:
        return bad("Authentication required", 401)

    # Look up the opportunity to snapshot role_title + company_name
    try:
        opp_resp = (
            supabase_client.table("opportunities")
            .select("id, title, company_name, created_by_user_id, organization_id")
            .eq("id", opportunity_id)
            .limit(1)
            .execute()
        )
    except Exception as e:
        return bad(f"Failed to fetch opportunity: {e}", 500)

    if not opp_resp.data:
        return bad("Opportunity not found", 404)
    opp = opp_resp.data[0]

    role_title = opp.get("title") or "Executive Search"
    company_name = opp.get("company_name")

    # Fall back to the organizations table if company_name wasn't denormalised
    if not company_name and opp.get("organization_id"):
        try:
            org_resp = (
                supabase_client.table("organizations")
                .select("name")
                .eq("id", opp["organization_id"])
                .limit(1)
                .execute()
            )
            if org_resp.data:
                company_name = org_resp.data[0].get("name")
        except Exception:
            pass

    company_name = company_name or "Your organisation"

    try:
        ins = (
            supabase_client.table("shortlists")
            .insert({
                "opportunity_id": opportunity_id,
                "created_by_user_id": user_id,
                "candidate_ids": candidate_ids,
                "message": message,
                "role_title": role_title,
                "company_name": company_name,
            })
            .execute()
        )
    except Exception as e:
        return bad(f"Failed to create shortlist: {e}", 500)

    row = (ins.data or [{}])[0]
    shortlist_id = row.get("id")
    expires_at = row.get("expires_at")

    # The public URL is built on the frontend side in practice — we
    # return a path so the frontend can prepend its own origin.
    public_path = f"/shortlist/{shortlist_id}"

    print(
        f"[SHORTLIST] created id={shortlist_id} opp={opportunity_id} "
        f"candidates={len(candidate_ids)}",
        flush=True,
    )

    return ok({
        "shortlist_id": shortlist_id,
        "path": public_path,
        "expires_at": expires_at,
        "role_title": role_title,
        "company_name": company_name,
    }, status=201)


# ── Public: read a shortlist ────────────────────────────────────────────────

@shortlist_bp.route("/shortlist/<shortlist_id>", methods=["GET"])
def get_shortlist(shortlist_id: str):
    """
    GET /shortlist/<shortlist_id>

    PUBLIC — no authentication. Returns the shortlist row plus hydrated
    candidate data (people_profiles + latest screening interaction).

    Shapes the response so the frontend can render directly without
    making additional calls.
    """
    if not supabase_client:
        return bad("Database not available", 503)

    try:
        sl_resp = (
            supabase_client.table("shortlists")
            .select(
                "id, opportunity_id, candidate_ids, message, role_title, "
                "company_name, expires_at, viewed_count, created_at"
            )
            .eq("id", shortlist_id)
            .limit(1)
            .execute()
        )
    except Exception as e:
        return bad(f"Failed to fetch shortlist: {e}", 500)

    if not sl_resp.data:
        return jsonify({"error": "Shortlist not found", "code": "not_found"}), 404

    sl = sl_resp.data[0]

    # Expiry check (application-level, not RLS)
    expires_at_str = sl.get("expires_at")
    if expires_at_str:
        try:
            expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
            if expires_at < datetime.now(timezone.utc):
                return jsonify({
                    "error": "This shortlist link has expired",
                    "code": "expired",
                }), 410
        except Exception:
            pass  # Non-fatal — if we can't parse, serve it

    candidate_ids = sl.get("candidate_ids") or []

    # Fetch candidate profiles
    candidates: list = []
    if candidate_ids:
        try:
            prof_resp = (
                supabase_client.table("people_profiles")
                .select(
                    "id, first_name, last_name, headline, location, "
                    "years_experience, bio, source_metadata"
                )
                .in_("id", candidate_ids)
                .execute()
            )
            profiles_by_id = {p["id"]: p for p in (prof_resp.data or [])}
        except Exception as e:
            print(f"[SHORTLIST] profiles fetch failed: {e}", flush=True)
            profiles_by_id = {}

        # Fetch latest screening interaction per candidate. interactions
        # doesn't have a direct FK to people_profiles in this schema —
        # the link is via outbound_call_jobs.artifacts.screening_context
        # candidate_id / source_candidate_id. We pull the most recent
        # 200 screening jobs and pick the newest one per candidate.
        screenings_by_candidate: dict = {}
        try:
            jobs_resp = (
                supabase_client.table("outbound_call_jobs")
                .select(
                    "id, status, artifacts, created_at, "
                    "interaction:interactions(id, transcript_text, "
                    "screening_scores, screening_recommendation, summary_text)"
                )
                .filter("artifacts->>call_type", "eq", "screening")
                .order("created_at", desc=True)
                .limit(500)
                .execute()
            )
            for job in (jobs_resp.data or []):
                ctx = (job.get("artifacts") or {}).get("screening_context") or {}
                cid = ctx.get("source_candidate_id") or ctx.get("candidate_id")
                if cid and cid in profiles_by_id and cid not in screenings_by_candidate:
                    ix = job.get("interaction")
                    if ix:
                        # Supabase returns joined tables as list if one-to-many
                        if isinstance(ix, list):
                            ix = ix[0] if ix else None
                        if ix:
                            screenings_by_candidate[cid] = ix
        except Exception as e:
            print(f"[SHORTLIST] screenings fetch failed: {e}", flush=True)

        # Build the response shape, preserving the order from candidate_ids
        for cid in candidate_ids:
            p = profiles_by_id.get(cid)
            if not p:
                continue
            name = " ".join(x for x in (p.get("first_name"), p.get("last_name")) if x).strip()
            fn = (p.get("first_name") or "").strip()
            ln = (p.get("last_name") or "").strip()
            initials = (
                ((fn[:1] + ln[:1]) or "??").upper()
            )

            ix = screenings_by_candidate.get(cid) or {}
            scores = ix.get("screening_scores")  # list of {name, score, weight}
            overall = None
            if isinstance(scores, list) and scores:
                try:
                    weighted = sum(
                        (s.get("score") or 0) * (s.get("weight") or 1)
                        for s in scores
                    )
                    total_w = sum((s.get("weight") or 1) for s in scores) or 1
                    overall = round(weighted / total_w, 2)
                except Exception:
                    overall = None

            candidates.append({
                "id": cid,
                "name": name or "Unnamed",
                "initials": initials,
                "headline": p.get("headline"),
                "location": p.get("location"),
                "years_experience": p.get("years_experience"),
                "bio": p.get("bio"),
                "scores": scores or [],
                "overall_score": overall,  # out of 5
                "recommendation": ix.get("screening_recommendation"),
                "summary": ix.get("summary_text"),
            })

    # Best-effort view-counter increment (never blocks the response)
    try:
        supabase_client.table("shortlists").update({
            "viewed_count": (sl.get("viewed_count") or 0) + 1,
            "last_viewed_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", shortlist_id).execute()
    except Exception as e:
        print(f"[SHORTLIST] view counter update failed: {e}", flush=True)

    return ok({
        "id": sl["id"],
        "role_title": sl.get("role_title"),
        "company_name": sl.get("company_name"),
        "message": sl.get("message"),
        "expires_at": sl.get("expires_at"),
        "candidates": candidates,
    }, status=200)


# ── Public: request an introduction from the shortlist page ─────────────────

@shortlist_bp.route("/shortlist/<shortlist_id>/request-intro", methods=["POST"])
def shortlist_request_intro(shortlist_id: str):
    """
    POST /shortlist/<shortlist_id>/request-intro

    PUBLIC — no authentication. Captures an introduction request from
    the client viewing the shortlist page. The employer sees it in
    their dashboard (or via email notification, future).

    Body (JSON):
      name:         str (required)
      email:        str (required)
      message:      str (optional)
      candidate_id: str (optional — specific candidate, else whole pack)
    """
    if not supabase_client:
        return bad("Database not available", 503)

    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    message = (data.get("message") or "").strip() or None
    candidate_id = (data.get("candidate_id") or "").strip() or None

    if not name or not email:
        return bad("name and email are required", 400)
    if "@" not in email:
        return bad("email is not valid", 400)

    # Verify the shortlist exists and isn't expired
    try:
        sl_resp = (
            supabase_client.table("shortlists")
            .select("id, expires_at")
            .eq("id", shortlist_id)
            .limit(1)
            .execute()
        )
    except Exception as e:
        return bad(f"Failed to fetch shortlist: {e}", 500)

    if not sl_resp.data:
        return bad("Shortlist not found", 404)

    expires_at_str = (sl_resp.data[0] or {}).get("expires_at")
    if expires_at_str:
        try:
            expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
            if expires_at < datetime.now(timezone.utc):
                return jsonify({"error": "Shortlist has expired"}), 410
        except Exception:
            pass

    try:
        supabase_client.table("shortlist_intro_requests").insert({
            "shortlist_id": shortlist_id,
            "candidate_id": candidate_id,
            "requester_name": name,
            "requester_email": email,
            "message": message,
        }).execute()
    except Exception as e:
        return bad(f"Failed to record intro request: {e}", 500)

    print(
        f"[SHORTLIST] intro request shortlist={shortlist_id} "
        f"candidate={candidate_id} email={email}",
        flush=True,
    )

    # Admin alert — fire-and-forget so email failure doesn't break the
    # public submission flow. We re-select the shortlist row fully
    # here (we only picked id + expires_at above) to get role_title
    # and company_name for the alert body.
    try:
        sl_full_resp = (
            supabase_client.table("shortlists")
            .select("role_title, company_name")
            .eq("id", shortlist_id)
            .limit(1)
            .execute()
        )
        sl_full = (sl_full_resp.data or [{}])[0]

        candidate_label = None
        if candidate_id:
            try:
                cand_resp = (
                    supabase_client.table("people_profiles")
                    .select("first_name, last_name")
                    .eq("id", candidate_id)
                    .limit(1)
                    .execute()
                )
                if cand_resp.data:
                    cand = cand_resp.data[0]
                    first = (cand.get("first_name") or "").strip()
                    last = (cand.get("last_name") or "").strip()
                    candidate_label = (f"{first} {last}").strip() or None
            except Exception as e:
                print(f"[SHORTLIST] candidate name lookup failed: {e}", flush=True)

        # Requester's company may have been supplied in the body — look
        # for it on the original request. Spec allows an optional
        # `company` field on the public form.
        requester_company = (data.get("company") or "").strip() or None

        from modules.email_sender import send_shortlist_intro_admin_alert
        send_shortlist_intro_admin_alert(
            requester_name=name,
            requester_email=email,
            requester_company=requester_company,
            candidate_name=candidate_label,
            role_title=sl_full.get("role_title"),
            company_name=sl_full.get("company_name"),
            message=message,
            shortlist_id=shortlist_id,
        )
    except Exception as e:
        print(f"[SHORTLIST] intro request admin alert failed: {e}", flush=True)

    return ok({"received": True}, status=201)
