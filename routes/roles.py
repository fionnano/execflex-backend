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

        # Get user_id from request or use test user for MVP (no auth required yet)
        # TODO: Replace with actual auth when authentication is implemented
        user_id = data.get("user_id") or "00000000-0000-0000-0000-000000000000"  # Test user UUID

        # Create or get company_profile first (if company info provided)
        company_id = None
        if data.get("company_name"):
            try:
                # Try to find existing company profile
                company_response = supabase_client.table("company_profiles").select("id").eq("user_id", user_id).eq("name", data["company_name"]).execute()
                
                if company_response.data and len(company_response.data) > 0:
                    company_id = company_response.data[0].get("id")
                else:
                    # Create new company profile
                    company_payload = {
                        "user_id": user_id,
                        "name": data["company_name"],
                        "mission": clean_optional(data.get("company_mission")),
                        "website": clean_optional(data.get("website")),
                        "linkedin": clean_optional(data.get("linkedin")),
                        "industry": [data["industry"]] if data.get("industry") else None,
                        "location": clean_optional(data.get("location")),
                    }
                    company_response = supabase_client.table("company_profiles").upsert(company_payload, on_conflict="user_id").execute()
                    if company_response.data:
                        company_id = company_response.data[0].get("id")
            except Exception as e:
                print(f"⚠️ Could not create/update company profile: {e}")

        # Determine opportunity_type (default to 'executive', can be 'board', 'ned', 'job')
        opportunity_type = data.get("opportunity_type", "executive")

        # Prepare Supabase payload with all fields
        supabase_payload = {
            "user_id": user_id,
            "role_title": data["role_title"],
            "company_id": company_id,
            "industry": data["industry"],
            "role_description": data["role_description"],
            "experience_level": data["experience_level"],
            "commitment_type": data["commitment"],
            "opportunity_type": opportunity_type,
            "is_remote": data.get("is_remote", False),
            "location": clean_optional(data.get("location")),
            "compensation": clean_optional(data.get("budget_range")),
            "role_type": data["role_type"],
            "contact_name": clean_optional(data.get("contact_name")),
            "contact_email": clean_optional(data.get("contact_email")),
            "phone": clean_optional(data.get("phone")),
            "linkedin": clean_optional(data.get("linkedin")),
            "website": clean_optional(data.get("website")),
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

