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

        # Get user_id from request or find an existing user for MVP (no auth required yet)
        # TODO: Replace with actual auth when authentication is implemented
        # Note: intros table has FK constraint to auth.users, so we need a real user_id
        user_id = data.get("user_id")
        
        if not user_id:
            # For smoke tests/MVP: Try to find any existing user_id from the database
            try:
                # Try to find a user_id from executive_profiles (most likely to have users)
                profiles_response = supabase_client.table("executive_profiles").select("user_id").limit(1).execute()
                if profiles_response.data and len(profiles_response.data) > 0:
                    user_id = profiles_response.data[0].get("user_id")
                else:
                    # Fallback: Try role_postings
                    roles_response = supabase_client.table("role_postings").select("user_id").limit(1).execute()
                    if roles_response.data and len(roles_response.data) > 0:
                        user_id = roles_response.data[0].get("user_id")
                    else:
                        # Last fallback: Try intros table itself
                        intros_response = supabase_client.table("intros").select("user_id").limit(1).execute()
                        if intros_response.data and len(intros_response.data) > 0:
                            user_id = intros_response.data[0].get("user_id")
            except Exception as e:
                print(f"⚠️ Could not find existing user for intro request: {e}")
        
        if not user_id:
            # If we still don't have a user_id, return a helpful error
            return bad("Unable to determine user_id. Please provide user_id in request. For MVP testing, ensure at least one user exists in the database.", 400)

        created = datetime.utcnow().isoformat() + "Z"
        record = {
            "user_id": user_id,
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

        # Fetch candidate details from match_id to send email
        candidate_name = "an executive"
        candidate_email = None
        candidate_role = None
        candidate_industries = []
        
        try:
            # Try to fetch candidate details from executive_profiles
            cand_response = supabase_client.table("executive_profiles").select(
                "first_name, last_name, email, contact_email, headline, industries"
            ).eq("id", data["match_id"]).execute()
            
            if cand_response.data and len(cand_response.data) > 0:
                cand = cand_response.data[0]
                first = cand.get("first_name") or ""
                last = cand.get("last_name") or ""
                candidate_name = " ".join([p for p in [first, last] if p]).strip() or "an executive"
                candidate_email = cand.get("email") or cand.get("contact_email")
                candidate_role = cand.get("headline") or None
                candidate_industries = cand.get("industries") or []
        except Exception as e:
            print(f"⚠️ Could not fetch candidate details: {e}")

        # Send introduction email if we have candidate email
        email_sent = False
        if candidate_email:
            try:
                email_sent = send_intro_email(
                    client_name=data["requester_name"],
                    client_email=data["requester_email"],
                    candidate_name=candidate_name,
                    candidate_email=candidate_email,
                    candidate_role=candidate_role,
                    candidate_industries=candidate_industries if isinstance(candidate_industries, list) else [],
                    requester_company=data.get("requester_company"),
                    user_type=data["user_type"],
                    match_id=data["match_id"],
                    body_extra=data.get("notes")
                )
                
                # Update status in Supabase
                if email_sent:
                    record["status"] = "sent"
                    try:
                        supabase_client.table("intros").update({"status": "sent"}).eq("id", intro_id).execute()
                    except Exception as e:
                        print(f"⚠️ Could not update intro status: {e}")
                else:
                    record["status"] = "failed"
                    try:
                        supabase_client.table("intros").update({"status": "failed"}).execute()
                    except Exception as e:
                        print(f"⚠️ Could not update intro status: {e}")
            except Exception as e:
                print(f"⚠️ Error sending intro email: {e}")
                record["status"] = "failed"
        else:
            print(f"⚠️ No candidate email found for match_id {data['match_id']}, email not sent")

        payload = {
            "intro_id": intro_id,
            "intro": record,
            "email_sent": email_sent
        }
        return ok(payload)

    except Exception as e:
        print("❌ /request-intro error:", e)
        return bad(str(e), 500)

