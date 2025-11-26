"""
Feedback submission route.
"""
from datetime import datetime
from flask import request
from routes import feedback_bp
from utils.response_helpers import ok, bad
from config.clients import supabase_client


@feedback_bp.route("/feedback", methods=["POST"])
def feedback():
    """
    Inserts feedback into Supabase using schema:
      user_name / match_name / feedback_text
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        user = data.get("user") or data.get("user_name")
        match = data.get("match") or data.get("match_name")
        fb = data.get("feedback") or data.get("feedback_text")

        if not all([user, match, fb]):
            return bad("Missing required fields: user/user_name, match/match_name, feedback/feedback_text")

        record = {
            "user_name": user,
            "match_name": match,
            "feedback_text": fb,
            "created_at": datetime.utcnow().isoformat() + "Z",
        }

        supabase_client.table("feedback").insert(record).execute()
        print("✅ Feedback saved to Supabase.")
        return ok({"status": "saved"})

    except Exception as e:
        print(f"❌ /feedback error: {e}")
        return bad(f"Failed to save feedback: {str(e)}", 500)

