"""
Introduction request routes.
"""
from datetime import datetime
from flask import request
from routes import introductions_bp
from utils.response_helpers import ok, bad
from config.clients import supabase_client
from modules.email_sender import send_intro_email


@introductions_bp.route("/request-intro", methods=["POST"])
def request_intro():
    """
    Stores an intro request in Supabase and optionally sends an email.
    Body (JSON):
      {
        "user_type": "client" | "candidate",
        "requester_name": "Jane Doe",
        "requester_email": "jane@acme.com",
        "requester_company": "Acme",
        "match_id": "cand-001",
        "notes": "Series B GTM help"
      }
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        required = ["user_type", "requester_name", "requester_email", "match_id"]
        missing = [f for f in required if not data.get(f)]
        if missing:
            return bad(f"Missing required fields: {', '.join(missing)}")

        created = datetime.utcnow().isoformat() + "Z"
        record = {
            "user_type": data["user_type"],
            "requester_name": data["requester_name"],
            "requester_email": data["requester_email"],
            "requester_company": data.get("requester_company"),
            "match_id": data["match_id"],
            "status": "pending",
            "notes": data.get("notes"),
            "created_at": created,
        }

        # Store intro request in Supabase
        try:
            res = supabase_client.table("intros").insert(record).execute()
            intro_id = res.data[0].get("id") if getattr(res, "data", None) else None
        except Exception as e:
            print(f"❌ Supabase insert failed (intros): {e}")
            return bad(f"Failed to store intro request: {str(e)}", 500)

        payload = {"intro_id": intro_id, "intro": record}
        return ok(payload)

    except Exception as e:
        print("❌ /request-intro error:", e)
        return bad(str(e), 500)


@introductions_bp.route("/send_intro", methods=["POST"])
def send_intro():
    """
    Legacy endpoint for sending intro emails.
    NOTE: This endpoint is deprecated. Use /request-intro instead.
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        client_name = data.get("client_name")
        match_name = data.get("match_name")
        client_email = data.get("email") or data.get("client_email")
        candidate_email = data.get("candidate_email")

        if not client_name or not match_name or not client_email:
            return bad("Missing required fields: client_name, match_name, email/client_email")

        # If candidate_email not provided, try to fetch from match_id or use placeholder
        if not candidate_email and data.get("match_id"):
            try:
                cand_response = supabase_client.table("executive_profiles").select("email, contact_email").eq("id", data.get("match_id")).execute()
                if cand_response.data and len(cand_response.data) > 0:
                    candidate_email = cand_response.data[0].get("email") or cand_response.data[0].get("contact_email")
            except Exception as e:
                print(f"⚠️ Could not fetch candidate email: {e}")

        if not candidate_email:
            candidate_email = "candidate@example.com"  # Fallback

        print(f"🚀 Sending intro: {client_name} ↔ {match_name} → {client_email}")
        success = send_intro_email(
            client_name=client_name,
            client_email=client_email,
            candidate_name=match_name,
            candidate_email=candidate_email,
            user_type=data.get("user_type", "client"),
            match_id=data.get("match_id")
        )
        return ok({"status": "success" if success else "fail"}, status=200 if success else 500)

    except Exception as e:
        print("❌ /send_intro error:", e)
        return bad(str(e), 500)

