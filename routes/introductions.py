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
    Creates a thread and interaction for an intro request, sends email.
    Body (JSON):
      {
        "user_type": "client" | "candidate",
        "requester_name": "Jane Doe",
        "requester_email": "jane@acme.com",
        "requester_company": "Acme",
        "match_id": "cand-001",  # people_profiles.id or user_id
        "notes": "Series B GTM help",
        "opportunity_id": "optional-opp-id"
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
        user_id = data.get("user_id")
        
        if not user_id:
            # For smoke tests/MVP: Try to find any existing user_id from the database
            try:
                # Try to find a user_id from people_profiles (most likely to have users)
                profiles_response = supabase_client.table("people_profiles").select("user_id").limit(1).execute()
                if profiles_response.data and len(profiles_response.data) > 0:
                    user_id = profiles_response.data[0].get("user_id")
                else:
                    # Fallback: Try opportunities
                    opps_response = supabase_client.table("opportunities").select("created_by_user_id").limit(1).execute()
                    if opps_response.data and len(opps_response.data) > 0:
                        user_id = opps_response.data[0].get("created_by_user_id")
                    else:
                        # Last fallback: Try threads table
                        threads_response = supabase_client.table("threads").select("primary_user_id").limit(1).execute()
                        if threads_response.data and len(threads_response.data) > 0:
                            user_id = threads_response.data[0].get("primary_user_id")
            except Exception as e:
                print(f"⚠️ Could not find existing user for intro request: {e}")
        
        if not user_id:
            # If we still don't have a user_id, return a helpful error
            return bad("Unable to determine user_id. Please provide user_id in request. For MVP testing, ensure at least one user exists in the database.", 400)

        # Fetch candidate details from people_profiles
        candidate_name = "an executive"
        candidate_email = None
        candidate_user_id = None
        candidate_role = None
        candidate_industries = []
        
        try:
            # Try to fetch candidate details from people_profiles
            # match_id could be a profile id or user_id
            cand_response = supabase_client.table("people_profiles").select(
                "id, user_id, first_name, last_name, headline, industries"
            ).or_(f"id.eq.{data['match_id']},user_id.eq.{data['match_id']}").limit(1).execute()
            
            if cand_response.data and len(cand_response.data) > 0:
                cand = cand_response.data[0]
                first = cand.get("first_name") or ""
                last = cand.get("last_name") or ""
                candidate_name = " ".join([p for p in [first, last] if p]).strip() or "an executive"
                candidate_user_id = cand.get("user_id")
                candidate_role = cand.get("headline") or None
                candidate_industries = cand.get("industries") or []
                
                # Try to get email from channel_identities
                if candidate_user_id:
                    email_response = supabase_client.table("channel_identities").select("value").eq("user_id", candidate_user_id).eq("channel", "email").limit(1).execute()
                    if email_response.data and len(email_response.data) > 0:
                        candidate_email = email_response.data[0].get("value")
        except Exception as e:
            print(f"⚠️ Could not fetch candidate details: {e}")

        # Get opportunity_id if provided
        opportunity_id = data.get("opportunity_id")

        # Create or find thread for this intro
        thread_id = None
        try:
            # Try to find existing thread for this user and candidate
            thread_subject = f"Introduction: {data['requester_name']} ↔ {candidate_name}"
            if opportunity_id:
                thread_subject = f"Opportunity Match: {candidate_name}"
            
            # Create new thread
            thread_payload = {
                "primary_user_id": user_id,
                "subject": thread_subject,
                "status": "open",
                "opportunity_id": opportunity_id,
                "active": True
            }
            thread_response = supabase_client.table("threads").insert(thread_payload).execute()
            if thread_response.data and len(thread_response.data) > 0:
                thread_id = thread_response.data[0].get("id")
        except Exception as e:
            print(f"⚠️ Could not create thread: {e}")
            return bad(f"Failed to create thread: {str(e)}", 500)

        if not thread_id:
            return bad("Failed to create thread", 500)

        # Send introduction email if we have candidate email
        email_sent = False
        interaction_id = None
        
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
                    body_extra=data.get("notes"),
                    thread_id=thread_id  # Pass thread_id for logging
                )
                
                # Create interaction record for the email
                try:
                    interaction_payload = {
                        "thread_id": thread_id,
                        "user_id": user_id,
                        "channel": "email",
                        "direction": "outbound",
                        "provider": "gmail",
                        "started_at": datetime.utcnow().isoformat() + "Z",
                        "ended_at": datetime.utcnow().isoformat() + "Z",
                        "summary_text": f"Introduction email sent from {data['requester_name']} ({data['requester_email']}) to {candidate_name} ({candidate_email})",
                        "artifacts": {
                            "recipient_email": data["requester_email"],
                            "candidate_email": candidate_email,
                            "candidate_name": candidate_name,
                            "status": "sent" if email_sent else "failed",
                            "notes": data.get("notes")
                        }
                    }
                    interaction_response = supabase_client.table("interactions").insert(interaction_payload).execute()
                    if interaction_response.data and len(interaction_response.data) > 0:
                        interaction_id = interaction_response.data[0].get("id")
                except Exception as e:
                    print(f"⚠️ Could not create interaction record: {e}")
                
                # Update thread status based on email result
                try:
                    new_status = "waiting_on_user" if email_sent else "open"
                    supabase_client.table("threads").update({"status": new_status}).eq("id", thread_id).execute()
                except Exception as e:
                    print(f"⚠️ Could not update thread status: {e}")
                    
            except Exception as e:
                print(f"⚠️ Error sending intro email: {e}")
        else:
            print(f"⚠️ No candidate email found for match_id {data['match_id']}, email not sent")

        payload = {
            "thread_id": thread_id,
            "interaction_id": interaction_id,
            "email_sent": email_sent,
            "status": "sent" if email_sent else "pending"
        }
        return ok(payload)

    except Exception as e:
        print("❌ /request-intro error:", e)
        return bad(str(e), 500)
