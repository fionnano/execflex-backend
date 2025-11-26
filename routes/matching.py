"""
Executive matching routes.
"""
from flask import request
from routes import matching_bp
from utils.response_helpers import ok, bad
from config.clients import supabase_client
from modules.match_finder import find_best_match


@matching_bp.route("/matches", methods=["GET"])
def matches():
    """Get matches - deprecated endpoint."""
    return bad("This endpoint is deprecated. Use POST /match instead.", 410)


@matching_bp.route("/matches/<match_id>", methods=["GET"])
def match_by_id(match_id):
    """Get a specific candidate by ID from Supabase."""
    try:
        response = supabase_client.table("executive_profiles").select("*").eq("id", match_id).execute()
        if response.data and len(response.data) > 0:
            return ok({"match": response.data[0]})
        return bad("Match not found", 404)
    except Exception as e:
        print(f"❌ Error fetching match {match_id}:", e)
        return bad(f"Failed to fetch match: {str(e)}", 500)


@matching_bp.route("/match", methods=["POST"])
def match():
    """Find best candidate match."""
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

