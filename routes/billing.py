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
