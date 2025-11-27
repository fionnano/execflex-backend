"""
Executive matching routes.
"""
from flask import request
from routes import matching_bp
from utils.response_helpers import ok, bad
from modules.match_finder import find_best_match


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

        matches = find_best_match(
            data["industry"],
            data["expertise"],
            data["availability"],
            min_experience,
            max_salary,
            data["location"],
        )

        # find_best_match returns a list of matches (up to 5)
        if matches and len(matches) > 0:
            # Return the top match as primary, but include all matches
            top_match = matches[0]
            return ok({
                "message": f"We recommend {top_match.get('name', 'a candidate')}: {top_match.get('summary', '')}",
                "match": top_match,
                "matches": matches  # Include all matches for flexibility
            })
        else:
            return ok({
                "message": "No match found yet. We'll follow up with suggestions soon.",
                "match": None,
                "matches": []
            })

    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"❌ /match error: {e}")
        print(f"❌ Full traceback:\n{error_details}")
        # Return user-friendly error message
        error_msg = str(e)
        if "Supabase" in error_msg or "SUPABASE" in error_msg:
            error_msg = "Database connection error. Please try again later."
        elif "permission" in error_msg.lower() or "policy" in error_msg.lower():
            error_msg = "Database access error. Please check configuration."
        return bad(f"Match request failed: {error_msg}", 500)

