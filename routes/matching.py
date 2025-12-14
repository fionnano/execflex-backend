"""
Executive matching routes.
"""
from flask import request
from routes import matching_bp
from utils.response_helpers import ok, bad
from utils.auth_helpers import require_auth
from modules.match_finder import find_best_match


@matching_bp.route("/match", methods=["POST"])
@require_auth
def match():
    """Find best candidate match."""
    try:
        data = request.get_json(force=True, silent=True) or {}
        
        # All fields are now optional - use empty strings/0 as defaults
        industry = data.get("industry", "") or ""
        expertise = data.get("expertise", "") or ""
        availability = data.get("availability", "") or ""
        location = data.get("location", "") or ""
        
        # Handle profileType filter (for NED/iNED filtering)
        profile_type = data.get("profileType", "") or ""
        is_ned_only = False
        if profile_type:
            # Check if profileType includes 'ned' (can be comma-separated)
            profile_types = [pt.strip().lower() for pt in profile_type.split(',') if pt.strip()]
            is_ned_only = 'ned' in profile_types or 'ined' in profile_types
        
        try:
            min_experience = int(data.get("min_experience", 0) or 0)
            max_salary = int(data.get("max_salary", 999999) or 999999)
        except (ValueError, TypeError):
            min_experience = 0
            max_salary = 999999

        matches = find_best_match(
            industry,
            expertise,
            availability,
            min_experience,
            max_salary,
            location,
            is_ned_only=is_ned_only,
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

