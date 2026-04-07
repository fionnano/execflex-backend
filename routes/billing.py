"""
Billing routes: Stripe Checkout, subscription management, and placement tracking.

POST /billing/create-checkout   — Create Stripe Checkout session
POST /billing/webhook           — Stripe webhook handler
GET  /billing/status            — Current subscription status + usage
POST /billing/customer-portal   — Create Stripe Customer Portal session
POST /admin/record-placement    — Record a placement (admin only)
GET  /admin/placements          — List placements (admin only)
"""
import os
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify
from utils.auth_helpers import require_auth, require_admin
from utils.response_helpers import ok, bad
from config.clients import supabase_client

billing_bp = Blueprint("billing", __name__)

# Lazy-init Stripe to avoid import error if stripe isn't installed yet
_stripe = None


def _get_stripe():
    global _stripe
    if _stripe is None:
        import stripe
        stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
        _stripe = stripe
    return _stripe


# ── Checkout ─────────────────────────────────────────────────────────────────

@billing_bp.route("/billing/create-checkout", methods=["POST"])
@require_auth
def create_checkout():
    """
    POST /billing/create-checkout

    Body (JSON):
        tier            str — "growth" (only supported tier for now)
        success_url     str — Redirect URL after successful payment
        cancel_url      str — Redirect URL if user cancels

    Returns:
        { checkout_url }
    """
    stripe = _get_stripe()
    if not stripe.api_key:
        return bad("Stripe is not configured", 503)

    data = request.get_json(force=True) or {}
    tier = data.get("tier", "growth")
    success_url = data.get("success_url")
    cancel_url = data.get("cancel_url")

    if not success_url or not cancel_url:
        return bad("success_url and cancel_url are required")

    price_map = {
        "growth": os.getenv("STRIPE_GROWTH_PRICE_ID"),
    }
    price_id = price_map.get(tier)
    if not price_id:
        return bad(f"Unknown tier: {tier}")

    user_id = request.environ.get("authenticated_user_id")

    # Find or create Stripe customer for this organization
    from services.billing_service import get_organization_for_user
    org = get_organization_for_user(user_id)
    customer_id = (org or {}).get("stripe_customer_id")

    try:
        if not customer_id:
            # Create a new Stripe customer
            customer = stripe.Customer.create(
                metadata={"user_id": user_id, "organization_id": (org or {}).get("id", "")},
            )
            customer_id = customer.id

            # Save customer ID to organization
            if org:
                supabase_client.table("organizations").update(
                    {"stripe_customer_id": customer_id}
                ).eq("id", org["id"]).execute()

        session = stripe.checkout.Session.create(
            customer=customer_id,
            mode="subscription",
            line_items=[{"price": price_id, "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={"user_id": user_id, "tier": tier},
        )

        return jsonify({"checkout_url": session.url}), 200

    except Exception as e:
        print(f"Stripe checkout error: {e}")
        return bad(f"Failed to create checkout session: {str(e)}", 500)


# ── Webhook ──────────────────────────────────────────────────────────────────

@billing_bp.route("/billing/webhook", methods=["POST"])
def stripe_webhook():
    """
    POST /billing/webhook

    Stripe sends events here. Verified via webhook signature.
    Handles: checkout.session.completed, customer.subscription.updated,
             customer.subscription.deleted
    """
    stripe = _get_stripe()
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")

    payload = request.get_data(as_text=True)
    sig_header = request.headers.get("Stripe-Signature")

    if not webhook_secret:
        print("WARNING: STRIPE_WEBHOOK_SECRET not set, skipping signature verification")
        try:
            import json
            event = json.loads(payload)
            # Wrap in a stripe-like object
            event = type("Event", (), {"type": event.get("type"), "data": type("Data", (), {"object": event.get("data", {}).get("object", {})})()})()
        except Exception as e:
            return bad(f"Invalid payload: {e}", 400)
    else:
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
        except ValueError:
            return bad("Invalid payload", 400)
        except stripe.error.SignatureVerificationError:
            return bad("Invalid signature", 400)

    event_type = event.type
    obj = event.data.object

    print(f"[Billing] Stripe event: {event_type}", flush=True)

    if event_type == "checkout.session.completed":
        _handle_checkout_completed(obj)
    elif event_type == "customer.subscription.updated":
        _handle_subscription_updated(obj)
    elif event_type == "customer.subscription.deleted":
        _handle_subscription_deleted(obj)

    return jsonify({"received": True}), 200


def _handle_checkout_completed(session):
    """Process a completed checkout — activate subscription."""
    customer_id = session.get("customer")
    subscription_id = session.get("subscription")
    tier = (session.get("metadata") or {}).get("tier", "growth")

    if not customer_id:
        return

    try:
        supabase_client.table("organizations").update({
            "stripe_subscription_id": subscription_id,
            "subscription_status": "active",
            "subscription_tier": tier,
        }).eq("stripe_customer_id", customer_id).execute()

        print(f"[Billing] Activated {tier} for customer {customer_id}", flush=True)
    except Exception as e:
        print(f"[Billing] Error updating org after checkout: {e}", flush=True)


def _handle_subscription_updated(subscription):
    """Process subscription status changes (upgrades, downgrades, trial end)."""
    customer_id = subscription.get("customer")
    status = subscription.get("status")  # active, past_due, canceled, trialing, etc.

    status_map = {
        "active": "active",
        "trialing": "trialing",
        "past_due": "active",  # still allow access during grace period
        "canceled": "canceled",
        "unpaid": "canceled",
    }
    mapped_status = status_map.get(status, "free")

    trial_end = subscription.get("trial_end")
    trial_ends_at = None
    if trial_end:
        trial_ends_at = datetime.fromtimestamp(trial_end, tz=timezone.utc).isoformat()

    try:
        update = {
            "subscription_status": mapped_status,
            "stripe_subscription_id": subscription.get("id"),
        }
        if trial_ends_at:
            update["trial_ends_at"] = trial_ends_at

        supabase_client.table("organizations").update(update).eq(
            "stripe_customer_id", customer_id
        ).execute()

        print(f"[Billing] Subscription updated: {customer_id} → {mapped_status}", flush=True)
    except Exception as e:
        print(f"[Billing] Error updating subscription: {e}", flush=True)


def _handle_subscription_deleted(subscription):
    """Process subscription cancellation — revert to free tier."""
    customer_id = subscription.get("customer")

    try:
        supabase_client.table("organizations").update({
            "subscription_status": "canceled",
            "subscription_tier": "free",
            "stripe_subscription_id": None,
        }).eq("stripe_customer_id", customer_id).execute()

        print(f"[Billing] Subscription canceled for {customer_id}", flush=True)
    except Exception as e:
        print(f"[Billing] Error processing cancellation: {e}", flush=True)


# ── Status ───────────────────────────────────────────────────────────────────

@billing_bp.route("/billing/status", methods=["GET"])
@require_auth
def billing_status():
    """
    GET /billing/status

    Returns current subscription tier, status, and usage counts.
    """
    user_id = request.environ.get("authenticated_user_id")

    from services.billing_service import get_organization_for_user, get_usage_this_month
    org = get_organization_for_user(user_id)
    usage = get_usage_this_month(user_id)

    return jsonify({
        "tier": (org or {}).get("subscription_tier", "free"),
        "status": (org or {}).get("subscription_status", "free"),
        "trial_ends_at": (org or {}).get("trial_ends_at"),
        "usage": usage,
    }), 200


# ── Customer Portal ──────────────────────────────────────────────────────────

@billing_bp.route("/billing/customer-portal", methods=["POST"])
@require_auth
def customer_portal():
    """
    POST /billing/customer-portal

    Creates a Stripe Customer Portal session for managing subscription.

    Body (JSON):
        return_url  str — URL to redirect back to after portal (optional)

    Returns:
        { portal_url }
    """
    stripe = _get_stripe()
    if not stripe.api_key:
        return bad("Stripe is not configured", 503)

    user_id = request.environ.get("authenticated_user_id")
    data = request.get_json(force=True) or {}
    return_url = data.get("return_url", os.getenv("FRONTEND_URL", "https://execflex.ai"))

    from services.billing_service import get_organization_for_user
    org = get_organization_for_user(user_id)
    customer_id = (org or {}).get("stripe_customer_id")

    if not customer_id:
        return bad("No billing account found. Subscribe first.", 404)

    try:
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url,
        )
        return jsonify({"portal_url": session.url}), 200
    except Exception as e:
        print(f"Stripe portal error: {e}")
        return bad(f"Failed to create portal session: {str(e)}", 500)


# ── Placements (Admin) ───────────────────────────────────────────────────────

@billing_bp.route("/admin/record-placement", methods=["POST"])
@require_admin
def record_placement():
    """
    POST /admin/record-placement

    Body (JSON):
        candidate_id      str — people_profiles user_id
        opportunity_id    str — opportunity UUID
        salary            num — Annual salary
        fee_percentage    num — Fee percentage (e.g. 15.0)
        notes             str — Optional notes
    """
    data = request.get_json(force=True) or {}

    required = ("candidate_id", "opportunity_id", "salary", "fee_percentage")
    missing = [f for f in required if data.get(f) is None]
    if missing:
        return bad(f"Missing required fields: {', '.join(missing)}")

    salary = float(data["salary"])
    fee_pct = float(data["fee_percentage"])
    fee_amount = round(salary * fee_pct / 100, 2)

    # Look up opportunity for role_title and organization_id
    role_title = "Unknown Role"
    organization_id = None
    try:
        opp_resp = (
            supabase_client.table("opportunities")
            .select("title, organization_id")
            .eq("id", data["opportunity_id"])
            .limit(1)
            .execute()
        )
        if opp_resp.data:
            role_title = opp_resp.data[0].get("title", role_title)
            organization_id = opp_resp.data[0].get("organization_id")
    except Exception:
        pass

    payload = {
        "organization_id": organization_id,
        "opportunity_id": data["opportunity_id"],
        "candidate_user_id": data["candidate_id"],
        "role_title": role_title,
        "annual_salary": salary,
        "fee_percentage": fee_pct,
        "fee_amount": fee_amount,
        "status": "pending",
        "placed_at": datetime.now(timezone.utc).isoformat(),
        "notes": data.get("notes"),
    }

    try:
        resp = supabase_client.table("placements").insert(payload).execute()
        placement = resp.data[0] if resp.data else payload
        return ok({"message": "Placement recorded", "placement": placement}, status=201)
    except Exception as e:
        print(f"Placement insert error: {e}")
        return bad(f"Failed to record placement: {str(e)}", 500)


@billing_bp.route("/admin/candidates/<candidate_id>/approve", methods=["POST"])
@require_admin
def approve_candidate(candidate_id: str):
    """
    POST /admin/candidates/<candidate_id>/approve

    Flip people_profiles.approved = True for this candidate so they
    become visible to POST /match. Returns the updated row.
    """
    return _set_candidate_approved(candidate_id, True)


@billing_bp.route("/admin/candidates/<candidate_id>/reject", methods=["POST"])
@require_admin
def reject_candidate(candidate_id: str):
    """
    POST /admin/candidates/<candidate_id>/reject

    Flip people_profiles.approved = False. Returns the updated row.
    """
    return _set_candidate_approved(candidate_id, False)


def _set_candidate_approved(candidate_id: str, approved: bool):
    """Shared update logic for approve/reject endpoints."""
    if not supabase_client:
        return bad("Database not available", 503)
    if not candidate_id:
        return bad("candidate_id is required", 400)
    try:
        resp = (
            supabase_client.table("people_profiles")
            .update({"approved": approved})
            .eq("id", candidate_id)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            return bad("Candidate not found", 404)
        print(
            f"[ADMIN] people_profiles.approved set to {approved} for id={candidate_id}",
            flush=True,
        )
        return ok({"candidate": rows[0]}, status=200)
    except Exception as e:
        print(f"[ADMIN] approve/reject candidate error: {e}", flush=True)
        return bad(f"Failed to update candidate: {str(e)}", 500)


@billing_bp.route("/admin/roles/<opportunity_id>/send-outreach", methods=["POST"])
@require_admin
def send_outreach_bulk(opportunity_id: str):
    """
    POST /admin/roles/<opportunity_id>/send-outreach

    Body: {"candidate_ids": ["uuid1", "uuid2", ...]}

    For each candidate_id:
      - Look up email (channel_identities by user_id/profile_id, then
        source_metadata.enriched_email, then source_metadata.personal_email)
      - If no email → skipped with reason 'no_email'
      - Generate LLM outreach email + append response links
      - Create a threads row for the outreach
      - Send the email via send_intro_email
      - Log an interactions row

    Returns {sent, skipped, total}.
    """
    if not supabase_client:
        return bad("Database not available", 503)

    data = request.get_json(force=True, silent=True) or {}
    candidate_ids = data.get("candidate_ids")
    if not isinstance(candidate_ids, list) or not candidate_ids:
        return bad("candidate_ids must be a non-empty array", 400)
    candidate_ids = [cid for cid in candidate_ids if isinstance(cid, str) and cid]
    if not candidate_ids:
        return bad("candidate_ids contains no valid ids", 400)

    # Fetch the opportunity + org name once for outreach context
    opportunity_record: dict = {}
    role_title = "the role"
    try:
        opp_resp = (
            supabase_client.table("opportunities")
            .select("id, title, description, location, compensation, industry, organization_id, metadata")
            .eq("id", opportunity_id)
            .limit(1)
            .execute()
        )
        if not opp_resp.data:
            return bad("Opportunity not found", 404)
        opportunity_record = opp_resp.data[0] or {}
        role_title = opportunity_record.get("title") or role_title
        org_id = opportunity_record.get("organization_id")
        if org_id:
            org_resp = (
                supabase_client.table("organizations")
                .select("name")
                .eq("id", org_id)
                .limit(1)
                .execute()
            )
            if org_resp.data:
                opportunity_record["company_name"] = org_resp.data[0].get("name")
    except Exception as e:
        print(f"[SEND-OUTREACH] opportunity lookup failed: {e}", flush=True)
        return bad(f"Failed to fetch opportunity: {e}", 500)

    # Fetch all candidate rows in one query
    try:
        cand_resp = (
            supabase_client.table("people_profiles")
            .select("id, user_id, first_name, last_name, headline, industries, years_experience, source_metadata")
            .in_("id", candidate_ids)
            .execute()
        )
        cand_rows = cand_resp.data or []
    except Exception as e:
        print(f"[SEND-OUTREACH] people_profiles lookup failed: {e}", flush=True)
        return bad(f"Failed to fetch candidates: {e}", 500)

    found_ids = {row["id"] for row in cand_rows}
    missing_ids = [cid for cid in candidate_ids if cid not in found_ids]

    sent: list = []
    skipped: list = []
    for mid in missing_ids:
        skipped.append({"id": mid, "reason": "not_found"})

    from services.outreach_service import generate_outreach_email, append_response_links
    from modules.email_sender import send_intro_email

    for row in cand_rows:
        cid = row["id"]
        first = (row.get("first_name") or "").strip()
        last = (row.get("last_name") or "").strip()
        candidate_name = (f"{first} {last}").strip() or "there"

        # Resolve email. Priority:
        # 1. source_metadata.enriched_email (PDL enrichment result)
        # 2. source_metadata.personal_email / source_metadata.work_email
        # 3. channel_identities by user_id
        email: str | None = None
        sm = row.get("source_metadata") or {}
        for key in ("enriched_email", "personal_email", "work_email"):
            v = sm.get(key)
            if isinstance(v, str) and "@" in v:
                email = v
                break
        if not email and row.get("user_id"):
            try:
                ci = (
                    supabase_client.table("channel_identities")
                    .select("value")
                    .eq("user_id", row["user_id"])
                    .eq("channel", "email")
                    .limit(1)
                    .execute()
                )
                if ci.data:
                    email = ci.data[0].get("value")
            except Exception as e:
                print(f"[SEND-OUTREACH] channel_identities lookup failed for {cid}: {e}", flush=True)

        if not email or "@" not in (email or ""):
            skipped.append({"id": cid, "reason": "no_email"})
            continue

        # Build outreach email
        candidate_profile = {
            "name": candidate_name,
            "headline": row.get("headline"),
            "years_experience": row.get("years_experience"),
            "industries": row.get("industries") or [],
        }
        try:
            outreach = generate_outreach_email(candidate_profile, opportunity_record)
            outreach_subject = outreach.get("subject")
            outreach_body = outreach.get("body") or ""
        except Exception as e:
            print(f"[SEND-OUTREACH] outreach generation failed for {cid}: {e}", flush=True)
            skipped.append({"id": cid, "reason": "outreach_generation_failed"})
            continue

        # Create the thread row first so we can embed its id in the links
        thread_id = None
        try:
            thread_payload = {
                "subject": f"Opportunity: {role_title}",
                "status": "outreach_sent",
                "opportunity_id": opportunity_id,
                "active": True,
            }
            # primary_user_id is only set for signup-path candidates; leave
            # it null for PDL-sourced rows.
            if row.get("user_id"):
                thread_payload["primary_user_id"] = row["user_id"]
            t_resp = supabase_client.table("threads").insert(thread_payload).execute()
            if t_resp.data:
                thread_id = t_resp.data[0].get("id")
        except Exception as e:
            print(f"[SEND-OUTREACH] thread insert failed for {cid}: {e}", flush=True)
            skipped.append({"id": cid, "reason": "thread_create_failed"})
            continue

        body_with_links = append_response_links(outreach_body, thread_id) if thread_id else outreach_body

        # Send the email
        try:
            email_sent = send_intro_email(
                client_name=candidate_name,
                client_email=email,
                candidate_name=candidate_name,
                candidate_email=email,
                subject=outreach_subject,
                candidate_role=row.get("headline"),
                requester_company=opportunity_record.get("company_name"),
                user_type="candidate",
                match_id=cid,
                thread_id=thread_id,
                plain_body_override=body_with_links or None,
            )
        except Exception as e:
            print(f"[SEND-OUTREACH] send_intro_email raised for {cid}: {e}", flush=True)
            email_sent = False

        if not email_sent:
            skipped.append({"id": cid, "reason": "send_failed"})
            continue

        # Log the interaction
        try:
            supabase_client.table("interactions").insert({
                "thread_id": thread_id,
                "user_id": row.get("user_id"),
                "channel": "email",
                "direction": "outbound",
                "provider": "gmail",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "ended_at": datetime.now(timezone.utc).isoformat(),
                "summary_text": f"Bulk outreach to {candidate_name} ({email}) for {role_title}",
                "artifacts": {
                    "candidate_profile_id": cid,
                    "candidate_email": email,
                    "candidate_name": candidate_name,
                    "outreach_email_subject": outreach_subject,
                    "outreach_email_body": body_with_links or outreach_body,
                    "source": "admin_bulk_outreach",
                },
            }).execute()
        except Exception as e:
            print(f"[SEND-OUTREACH] interaction insert failed for {cid}: {e}", flush=True)

        sent.append(cid)

    print(
        f"[SEND-OUTREACH] opportunity={opportunity_id} sent={len(sent)} "
        f"skipped={len(skipped)}",
        flush=True,
    )

    return ok({
        "opportunity_id": opportunity_id,
        "sent": sent,
        "skipped": skipped,
        "total": len(candidate_ids),
    }, status=200)


@billing_bp.route("/admin/roles/<opportunity_id>/approve-sourced", methods=["POST"])
@require_admin
def approve_sourced_candidates(opportunity_id: str):
    """
    POST /admin/roles/<opportunity_id>/approve-sourced

    Body: {"candidate_ids": ["uuid1", "uuid2", ...]}

    Sets approved=True for every listed candidate_id whose
    source_metadata->>'opportunity_id' matches the route param.
    Any id that doesn't match the opportunity is silently skipped
    so an admin can't accidentally approve candidates sourced for
    a different role.
    """
    if not supabase_client:
        return bad("Database not available", 503)

    data = request.get_json(force=True, silent=True) or {}
    candidate_ids = data.get("candidate_ids")
    if not isinstance(candidate_ids, list) or not candidate_ids:
        return bad("candidate_ids must be a non-empty array", 400)

    # De-dupe and sanity-check
    candidate_ids = [cid for cid in candidate_ids if isinstance(cid, str) and cid]
    if not candidate_ids:
        return bad("candidate_ids contains no valid ids", 400)

    try:
        # Fetch the matching rows first so we can enforce the
        # opportunity_id guard and return the updated rows.
        existing_resp = (
            supabase_client.table("people_profiles")
            .select("id, source_metadata")
            .in_("id", candidate_ids)
            .execute()
        )
        existing = existing_resp.data or []

        eligible_ids = []
        for row in existing:
            sm = row.get("source_metadata") or {}
            opps = sm.get("opportunity_ids")
            primary_opp = sm.get("opportunity_id")
            if primary_opp == opportunity_id:
                eligible_ids.append(row["id"])
            elif isinstance(opps, list) and opportunity_id in opps:
                eligible_ids.append(row["id"])

        skipped_ids = [cid for cid in candidate_ids if cid not in eligible_ids]

        if not eligible_ids:
            return bad(
                f"No candidates found for opportunity {opportunity_id}",
                404,
            )

        update_resp = (
            supabase_client.table("people_profiles")
            .update({"approved": True})
            .in_("id", eligible_ids)
            .execute()
        )
        updated_rows = update_resp.data or []

        print(
            f"[ADMIN] Bulk-approved {len(updated_rows)} sourced candidates "
            f"for opportunity={opportunity_id} skipped={len(skipped_ids)}",
            flush=True,
        )

        return ok({
            "opportunity_id": opportunity_id,
            "approved_count": len(updated_rows),
            "approved_ids": eligible_ids,
            "skipped_ids": skipped_ids,
            "candidates": updated_rows,
        }, status=200)

    except Exception as e:
        print(f"[ADMIN] approve-sourced error: {e}", flush=True)
        return bad(f"Failed to bulk-approve candidates: {str(e)}", 500)


@billing_bp.route("/admin/placements/<placement_id>", methods=["PATCH"])
@require_admin
def update_placement(placement_id: str):
    """
    PATCH /admin/placements/<placement_id>

    Body: {"status": "pending" | "invoiced" | "paid"}

    Transitions a placement through the billing lifecycle. Sets
    updated_at on every successful change. Returns the updated row.
    """
    if not supabase_client:
        return bad("Database not available", 503)
    if not placement_id:
        return bad("placement_id is required", 400)

    data = request.get_json(force=True, silent=True) or {}
    new_status = (data.get("status") or "").strip().lower()

    allowed = {"pending", "invoiced", "paid"}
    if new_status not in allowed:
        return bad(
            f"Invalid status '{new_status}'. Must be one of: {', '.join(sorted(allowed))}",
            400,
        )

    try:
        payload = {
            "status": new_status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        resp = (
            supabase_client.table("placements")
            .update(payload)
            .eq("id", placement_id)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            return bad("Placement not found", 404)
        print(
            f"[ADMIN] placement {placement_id} status → {new_status}",
            flush=True,
        )
        return ok({"placement": rows[0]}, status=200)
    except Exception as e:
        print(f"[ADMIN] update placement error: {e}", flush=True)
        return bad(f"Failed to update placement: {str(e)}", 500)


@billing_bp.route("/admin/placements", methods=["GET"])
@require_admin
def list_placements():
    """
    GET /admin/placements

    Query params:
        status  str — Filter by status (pending/invoiced/paid)
        limit   int — Max results (default 50)
        offset  int — Pagination offset (default 0)
    """
    status_filter = request.args.get("status")
    limit = min(int(request.args.get("limit", 50)), 200)
    offset = int(request.args.get("offset", 0))

    try:
        query = supabase_client.table("placements").select("*", count="exact")
        if status_filter:
            query = query.eq("status", status_filter)
        query = query.order("placed_at", desc=True).range(offset, offset + limit - 1)
        resp = query.execute()

        return jsonify({
            "placements": resp.data or [],
            "total": resp.count if resp.count is not None else len(resp.data or []),
        }), 200
    except Exception as e:
        print(f"Placements list error: {e}")
        return bad(f"Failed to list placements: {str(e)}", 500)
