"""
Role posting routes.
"""
from datetime import datetime
from flask import request
from routes import roles_bp
from utils.response_helpers import ok, bad
from config.clients import supabase_client


@roles_bp.route("/post-role", methods=["POST"])
def post_role():
    """Submit a new executive role posting."""
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

        # Save to Supabase and return the created record
        try:
            response = supabase_client.table("role_postings").insert(supabase_payload).execute()
            print("✅ Saved to Supabase (role_postings).")
            
            # Supabase insert returns the created record(s) in response.data
            created_record = response.data[0] if response.data and len(response.data) > 0 else None
            
            if created_record:
                return ok({
                    "message": "Role posted successfully!",
                    "role": created_record
                }, status=201)
            else:
                # Fallback if response doesn't include the record
                return ok({"message": "Role posted successfully!"}, status=201)
        except Exception as e:
            print(f"❌ Supabase insert failed (role_postings): {e}")
            return bad(f"Failed to save role posting: {str(e)}", 500)

    except Exception as e:
        print("❌ /post-role error:", e)
        return bad(str(e), 500)

