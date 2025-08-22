import os
import json
from datetime import datetime
from dotenv import load_dotenv
from flask import Flask, request, jsonify
from flask_cors import CORS

# Your existing modules
from modules.role_details import RoleDetails
from modules.match_finder import find_best_match
from modules.email_sender import send_intro_email

# Supabase (optional but supported)
try:
    from supabase import create_client, Client  # type: ignore
except Exception:
    create_client = None
    Client = None

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

app = Flask(__name__)
# MVP: allow all; lock down to your Lovable domain later
CORS(app, resources={r"/*": {"origins": "*"}})

# Create Supabase client if possible
supabase = None
if SUPABASE_URL and SUPABASE_KEY and create_client:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("✅ Supabase client initialised.")
    except Exception as e:
        print("❌ Supabase init failed:", e)
        supabase = None
else:
    print("ℹ️ Supabase not configured (this is fine for local/demo).")

# Path to local demo candidates
MATCHES_PATH = os.path.join(os.path.dirname(__file__), "matches.json")


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


def load_matches_json():
    if not os.path.exists(MATCHES_PATH):
        sample = [
            {
                "id": "cand-001",
                "name": "Alex Byrne",
                "role": "Fractional CRO",
                "industry": ["saas", "fintech"],
                "culture": ["hands-on", "data-led"],
                "summary": "18+ years scaling B2B SaaS revenue from Series A to C.",
                "highlights": ["Built SDR->AE engine", "RevOps discipline", "EMEA expansion"],
                "location": "Dublin, IE",
            }
        ]
        with open(MATCHES_PATH, "w", encoding="utf-8") as f:
            json.dump(sample, f, indent=2)
        return sample

    try:
        with open(MATCHES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("❌ Failed to read matches.json:", e)
        return []


def score_candidate(m, role=None, industry=None, culture=None):
    s = 0
    if role and role.lower() in (m.get("role", "").lower()):
        s += 3
    if industry and industry.lower() in [i.lower() for i in m.get("industry", [])]:
        s += 2
    if culture and culture.lower() in [c.lower() for c in m.get("culture", [])]:
        s += 1
    return s


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
    role = request.args.get("role")
    industry = request.args.get("industry")
    culture = request.args.get("culture")

    items = load_matches_json()
    for m in items:
        m["_score"] = score_candidate(m, role, industry, culture)
    items.sort(key=lambda x: x.get("_score", 0), reverse=True)
    results = [{k: v for k, v in m.items() if not k.startswith("_")} for m in items if m.get("_score", 0) > 0]
    if not results:
        results = [{k: v for k, v in m.items() if not k.startswith("_")} for m in items[:5]]
    return ok({"matches": results})


@app.route("/matches/<match_id>", methods=["GET"])
def match_by_id(match_id):
    for m in load_matches_json():
        if m.get("id") == match_id:
            return ok({"match": m})
    return bad("Match not found", 404)


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

        intro_id = None
        if supabase:
            try:
                res = supabase.table("intros").insert(record).execute()
                if getattr(res, "data", None):
                    intro_id = res.data[0].get("id")
            except Exception as e:
                print("⚠️ Supabase insert failed (intros). Continuing.", e)

        # Optional confirmation email to the requester
        try:
            cand = next((m for m in load_matches_json() if m.get("id") == data["match_id"]), None)
            cand_name = cand["name"] if cand else data["match_id"]
            send_intro_email(data["requester_name"], cand_name, data["requester_email"])
        except Exception as e:
            print("⚠️ send_intro_email failed (non-fatal):", e)

        payload = {"intro_id": intro_id, "intro": record}
        return ok(payload)

    except Exception as e:
        print("❌ /request-intro error:", e)
        return bad(str(e), 500)


@app.route("/feedback", methods=["POST"])
def feedback():
    """
    Inserts feedback into Supabase. First tries your existing schema:
      user_name / match_name / feedback_text
    Falls back to a simpler schema if needed: user / match / feedback
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        user = data.get("user")
        match = data.get("match")
        fb = data.get("feedback")

        if not all([user, match, fb]):
            return bad("Missing required fields: user, match, feedback")

        created = datetime.utcnow().isoformat() + "Z"

        if supabase:
            # 1) Try your existing column names (seen in your screenshot)
            try:
                rec1 = {
                    "user_name": user,
                    "match_name": match,
                    "feedback_text": fb,
                    "created_at": created,
                }
                supabase.table("feedback").insert(rec1).execute()
                print("✅ Feedback saved (user_name/match_name/feedback_text).")
            except Exception as e1:
                print("⚠️ Insert to feedback (user_name/match_name/feedback_text) failed:", e1)
                # 2) Fallback to simple column names if the first insert fails
                try:
                    rec2 = {
                        "user": user,
                        "match": match,
                        "feedback": fb,
                        "created_at": created,
                    }
                    supabase.table("feedback").insert(rec2).execute()
                    print("✅ Feedback saved (user/match/feedback).")
                except Exception as e2:
                    print("❌ Both feedback insert attempts failed:", e2)
                    return bad("Could not save feedback.", 500)

        else:
            print("ℹ️ Supabase not configured; feedback accepted (not stored).")

        return ok({"status": "saved"})

    except Exception as e:
        print("❌ /feedback error:", e)
        return bad(str(e), 500)


@app.route("/post-role", methods=["POST"])
def post_role():
    try:
        data = request.get_json(force=True, silent=True) or {}
        print("🚀 /post-role payload:", data)

        required_fields = [
            "role_title", "company_name", "industry", "role_description",
            "experience_level", "commitment", "location", "budget_range",
            "role_type", "contact_name", "contact_email"
        ]
        missing = [f for f in required_fields if f not in data]
        if missing:
            return bad(f"Missing required fields: {', '.join(missing)}")

        # Local fallback write (keep simple audit)
        role_details = RoleDetails(
            role_title=data["role_title"],
            company_name=data["company_name"],
            industry=data["industry"],
            role_description=data["role_description"],
            experience_level=data["experience_level"],
            commitment=data["commitment"],
            location=data["location"],
            budget_range=data["budget_range"],
            role_type=data["role_type"],
            contact_name=data["contact_name"],
            contact_email=data["contact_email"],
        )

        try:
            items = load_matches_json()
            items.append(role_details.to_dict())
            with open(MATCHES_PATH, "w", encoding="utf-8") as f:
                json.dump(items, f, indent=2)
        except Exception as e:
            print("⚠️ Local save failed (matches.json). Continuing.", e)

        if supabase:
            try:
                supabase_payload = {
                    "role_title": data["role_title"],
                    "company_name": data["company_name"],
                    "industry": data["industry"],
                    "role_description": data["role_description"],
                    "experience_level": data["experience_level"],
                    "commitment_type": data["commitment"],
                    "is_remote": data.get("is_remote", False),
                    "location": data["location"],
                    "compensation": data["budget_range"],
                    "role_type": data["role_type"],
                    "contact_name": data["contact_name"],
                    "contact_email": data["contact_email"],
                    "phone": data.get("phone"),
                    "linkedin": data.get("linkedin"),
                    "website": data.get("website"),
                    "company_mission": data.get("company_mission"),
                    "created_at": datetime.utcnow().isoformat() + "Z",
                }
                supabase.table("role_postings").insert(supabase_payload).execute()
                print("✅ Saved to Supabase (role_postings).")
            except Exception as e:
                print("⚠️ Supabase insert failed (role_postings). Continuing.", e)

        return ok({"message": "Role posted successfully!"}, status=201)

    except Exception as e:
        print("❌ /post-role error:", e)
        return bad(str(e), 500)


@app.route("/view-roles", methods=["GET"])
def view_roles():
    try:
        if supabase:
            try:
                response = supabase.table("role_postings").select("*").order("created_at", desc=True).execute()
                roles = response.data or []
                return ok({"roles": roles})
            except Exception as e:
                print("⚠️ Supabase select failed (role_postings). Falling back to local.", e)

        items = load_matches_json()
        roles = [i for i in items if "role_title" in i and "company_name" in i]
        return ok({"roles": roles})

    except Exception as e:
        print("❌ /view-roles error:", e)
        return bad(str(e), 500)


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=True)
