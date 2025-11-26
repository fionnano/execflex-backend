import os
from datetime import datetime
from dotenv import load_dotenv
from flask import Flask, request, jsonify
from flask_cors import CORS

# Your existing modules
from modules.match_finder import find_best_match
from modules.email_sender import send_intro_email

# Supabase (required)
try:
    from supabase import create_client, Client  # type: ignore
except ImportError as e:
    raise ImportError("Supabase client is required. Install: pip install supabase") from e

# -------------------- ENV & INIT --------------------
load_dotenv()

APP_ENV = os.getenv("APP_ENV", "dev")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")  # Service role key recommended for server-side
EMAIL_ADDRESS = os.getenv("EMAIL_USER")

print("✅ server.py booting...")
print(f"APP_ENV={APP_ENV}")
print(f"Email User={EMAIL_ADDRESS}")
print(f"Supabase URL present? {bool(SUPABASE_URL)}")
print("--------------------------------------------------")

# Validate Supabase configuration
if not SUPABASE_URL:
    raise ValueError("SUPABASE_URL environment variable is required")
if not SUPABASE_KEY:
    raise ValueError("SUPABASE_SERVICE_KEY environment variable is required")

app = Flask(__name__)
# MVP: allow all; lock down to your Lovable domain later
CORS(app, resources={r"/*": {"origins": "*"}})

# Create Supabase client (required)
try:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ Supabase client initialised.")
except Exception as e:
    raise RuntimeError(f"Failed to initialize Supabase client: {e}") from e


# -------------------- UTILITIES --------------------
def ok(payload=None, status=200, **extra):
    data = {"ok": True}
    if payload:
        data.update(payload)
    if extra:
        data.update(extra)
    return jsonify(data), status


def bad(message, status=400, **extra):
    data = {"ok": False, "error": message}
    if extra:
        data.update(extra)
    return jsonify(data), status






# -------------------- ROUTES --------------------
@app.route("/", methods=["GET"])
def root_health():
    return "✅ Backend is live!", 200


@app.route("/health", methods=["GET"])
def health():
    return ok({
        "env": APP_ENV,
        "supabase_connected": bool(supabase),
    })


@app.route("/matches", methods=["GET"])
def matches():
    """
    Get matches using the match_finder module (Supabase required).
    This endpoint is deprecated - use /match POST instead.
    """
    return bad("This endpoint is deprecated. Use POST /match instead.", 410)


@app.route("/matches/<match_id>", methods=["GET"])
def match_by_id(match_id):
    """
    Get a specific candidate by ID from Supabase.
    """
    try:
        response = supabase.table("executive_profiles").select("*").eq("id", match_id).execute()
        if response.data and len(response.data) > 0:
            return ok({"match": response.data[0]})
        return bad("Match not found", 404)
    except Exception as e:
        print(f"❌ Error fetching match {match_id}:", e)
        return bad(f"Failed to fetch match: {str(e)}", 500)


@app.route("/match", methods=["POST"])
def match():
    try:
        data = request.get_json(force=True, silent=True) or {}
        required = ["industry", "expertise", "availability", "min_experience", "max_salary", "location"]
        missing = [f for f in required if not data.get(f)]
        if missing:
            return bad(f"Missing or invalid data for: {', '.join(missing)}")

        try:
            min_experience = int(data["min_experience"])
            max_salary = int(data["max_salary"])
        except Exception:
            return bad("min_experience and max_salary must be numbers.")

        result = find_best_match(
            data["industry"],
            data["expertise"],
            data["availability"],
            min_experience,
            max_salary,
            data["location"],
        )

        if result:
            return ok({
                "message": f"We recommend {result['name']}: {result['summary']}",
                "match": result
            })
        else:
            return ok({"message": "No match found yet. We'll follow up with suggestions soon.", "match": None})

    except Exception as e:
        print("❌ /match error:", e)
        return bad(str(e), 500)


@app.route("/send_intro", methods=["POST"])
def send_intro():
    try:
        data = request.get_json(force=True, silent=True) or {}
        client_name = data.get("client_name")
        match_name = data.get("match_name")
        email = data.get("email")

        if not client_name or not match_name or not email:
            return bad("Missing required fields: client_name, match_name, email")

        print(f"🚀 Sending intro: {client_name} ↔ {match_name} → {email}")
        success = send_intro_email(client_name, match_name, email)
        return ok({"status": "success" if success else "fail"}, status=200 if success else 500)

    except Exception as e:
        print("❌ /send_intro error:", e)
        return bad(str(e), 500)


@app.route("/request-intro", methods=["POST"])
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
            res = supabase.table("intros").insert(record).execute()
            intro_id = res.data[0].get("id") if getattr(res, "data", None) else None
        except Exception as e:
            print(f"❌ Supabase insert failed (intros): {e}")
            return bad(f"Failed to store intro request: {str(e)}", 500)

        # Optional confirmation email to the requester
        # TODO: turn on confirmation email
        # try:
        #     # Fetch candidate name from Supabase
        #     cand_response = supabase.table("executive_profiles").select("first_name, last_name").eq("id", data["match_id"]).execute()
        #     cand_name = data["match_id"]  # default if not found
        #     if cand_response.data and len(cand_response.data) > 0:
        #         cand = cand_response.data[0]
        #         first = cand.get("first_name", "")
        #         last = cand.get("last_name", "")
        #         cand_name = " ".join([p for p in [first, last] if p]).strip() or cand_name
        #     send_intro_email(data["requester_name"], cand_name, data["requester_email"])
        # except Exception as e:
        #     print(f"⚠️ send_intro_email failed (non-fatal): {e}")

        payload = {"intro_id": intro_id, "intro": record}
        return ok(payload)

    except Exception as e:
        print("❌ /request-intro error:", e)
        return bad(str(e), 500)


@app.route("/feedback", methods=["POST"])
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

        supabase.table("feedback").insert(record).execute()
        print("✅ Feedback saved to Supabase.")
        return ok({"status": "saved"})

    except Exception as e:
        print(f"❌ /feedback error: {e}")
        return bad(f"Failed to save feedback: {str(e)}", 500)


@app.route("/post-role", methods=["POST"])
def post_role():
    try:
        data = request.get_json(force=True, silent=True) or {}
        print("🚀 /post-role payload:", data)

        # Only require truly essential fields
        required_fields = [
            "role_title", "industry", "role_description",
            "experience_level", "commitment", "role_type"
        ]
        missing = [f for f in required_fields if f not in data or not data.get(f)]
        if missing:
            return bad(f"Missing required fields: {', '.join(missing)}")

        # Helper to clean optional fields (convert "Not Specified"/"Not Provided" to None)
        def clean_optional(value):
            if not value or value in ["Not Specified", "Not Provided", ""]:
                return None
            return value

        # Prepare Supabase payload with all fields
        supabase_payload = {
            "role_title": data["role_title"],
            "company_name": clean_optional(data.get("company_name")),
            "industry": data["industry"],
            "role_description": data["role_description"],
            "experience_level": data["experience_level"],
            "commitment_type": data["commitment"],
            "is_remote": data.get("is_remote", False),
            "location": clean_optional(data.get("location")),
            "compensation": clean_optional(data.get("budget_range")),
            "role_type": data["role_type"],
            "contact_name": clean_optional(data.get("contact_name")),
            "contact_email": clean_optional(data.get("contact_email")),
            "phone": clean_optional(data.get("phone")),
            "linkedin": clean_optional(data.get("linkedin")),
            "website": clean_optional(data.get("website")),
            "company_mission": clean_optional(data.get("company_mission")),
            "created_at": datetime.utcnow().isoformat() + "Z",
        }

        # Save to Supabase
        try:
            supabase.table("role_postings").insert(supabase_payload).execute()
            print("✅ Saved to Supabase (role_postings).")
        except Exception as e:
            print(f"❌ Supabase insert failed (role_postings): {e}")
            return bad(f"Failed to save role posting: {str(e)}", 500)

        return ok({"message": "Role posted successfully!"}, status=201)

    except Exception as e:
        print("❌ /post-role error:", e)
        return bad(str(e), 500)


@app.route("/view-roles", methods=["GET"])
def view_roles():
    """
    Retrieve all role postings from Supabase.
    """
    try:
        response = supabase.table("role_postings").select("*").order("created_at", desc=True).execute()
        roles = response.data or []
        return ok({"roles": roles})
    except Exception as e:
        print(f"❌ /view-roles error: {e}")
        return bad(f"Failed to fetch role postings: {str(e)}", 500)


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=True)
