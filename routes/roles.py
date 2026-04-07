"""
Role posting routes.
"""
from datetime import datetime
from flask import request, jsonify
from routes import roles_bp
from utils.response_helpers import ok, bad
from utils.auth_helpers import require_auth
from config.clients import supabase_client


@roles_bp.route("/post-role", methods=["POST"])
@require_auth
def post_role():
    """Submit a new executive role posting."""
    try:
        # Tier quota check
        user_id = request.environ.get("authenticated_user_id")
        from services.billing_service import check_quota
        allowed, quota_msg = check_quota(user_id, "roles_posted")
        if not allowed:
            return bad(quota_msg, 403, error_code="upgrade_required", upgrade_url="/pricing")

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

        # Get user_id from authenticated JWT token
        user_id = request.environ.get('authenticated_user_id')
        if not user_id:
            return bad("Authentication required", 401)

        # Create or get organization first (if company info provided)
        organization_id = None
        if data.get("company_name"):
            try:
                # Try to find existing organization
                org_response = supabase_client.table("organizations").select("id").eq("name", data["company_name"]).execute()
                
                if org_response.data and len(org_response.data) > 0:
                    organization_id = org_response.data[0].get("id")
                else:
                    # Create new organization (organizations table doesn't have user_id)
                    org_payload = {
                        "name": data["company_name"],
                        "mission": clean_optional(data.get("company_mission")),
                        "website": clean_optional(data.get("website")),
                        "linkedin": clean_optional(data.get("linkedin")),
                        "industry": data.get("industry"),
                        "location": clean_optional(data.get("location")),
                    }
                    org_response = supabase_client.table("organizations").insert(org_payload).execute()
                    if org_response.data:
                        organization_id = org_response.data[0].get("id")
            except Exception as e:
                print(f"⚠️ Could not create/update organization: {e}")

        # Determine opportunity type (default to 'hire_fractional', map old types)
        opp_type_map = {
            "executive": "hire_fractional",
            "board": "hire_ned",
            "ned": "hire_ned",
            "job": "general"
        }
        opportunity_type = opp_type_map.get(data.get("opportunity_type", "executive"), "hire_fractional")

        # Prepare Supabase payload for opportunities table
        supabase_payload = {
            "created_by_user_id": user_id,
            "organization_id": organization_id,
            "type": opportunity_type,
            "title": data["role_title"],
            "description": data["role_description"],
            "industry": data["industry"],
            "location": clean_optional(data.get("location")),
            "is_remote": data.get("is_remote", False),
            "commitment_type": data["commitment"],
            "compensation": clean_optional(data.get("budget_range")),
            "status": "open",
            "metadata": {
                "experience_level": data.get("experience_level"),
                "role_type": data.get("role_type"),
                "contact_name": clean_optional(data.get("contact_name")),
                "contact_email": clean_optional(data.get("contact_email")),
                "phone": clean_optional(data.get("phone")),
                "linkedin": clean_optional(data.get("linkedin")),
                "website": clean_optional(data.get("website")),
            }
        }

        # Save to Supabase and return the created record
        try:
            response = supabase_client.table("opportunities").insert(supabase_payload).execute()
            print("✅ Saved to Supabase (opportunities).")
            
            # Supabase insert returns the created record(s) in response.data
            created_record = response.data[0] if response.data and len(response.data) > 0 else None
            
            # Fire-and-forget Apollo sourcing (best-effort, never blocks the response)
            if created_record and created_record.get("id"):
                try:
                    from services.apollo_service import (
                        source_and_upsert_async,
                        get_seniority_from_title,
                    )
                    source_and_upsert_async(
                        opportunity_id=created_record["id"],
                        role_title=data["role_title"],
                        location=clean_optional(data.get("location")),
                        seniority_levels=get_seniority_from_title(data["role_title"]),
                    )
                except Exception as e:
                    print(f"⚠️ Apollo sourcing dispatch failed: {e}")

            if created_record:
                return ok({
                    "message": "Role posted successfully!",
                    "role": created_record
                }, status=201)
            else:
                # Fallback if response doesn't include the record
                return ok({"message": "Role posted successfully!"}, status=201)
        except Exception as e:
            print(f"❌ Supabase insert failed (opportunities): {e}")
            return bad(f"Failed to save opportunity: {str(e)}", 500)

    except Exception as e:
        print("❌ /post-role error:", e)
        return bad(str(e), 500)


@roles_bp.route("/roles/<opportunity_id>/sourced-candidates", methods=["GET"])
@require_auth
def sourced_candidates(opportunity_id: str):
    """
    GET /roles/<opportunity_id>/sourced-candidates

    Return Apollo-sourced candidate suggestions for an opportunity.
    Ordered by years_experience DESC (NULLS LAST), limit 20.
    """
    try:
        if not supabase_client:
            return bad("Database not available", 503)

        resp = (
            supabase_client.table("people_profiles")
            .select(
                "id, first_name, last_name, headline, location, "
                "years_experience, approved, source_metadata"
            )
            .eq("source", "apollo")
            .eq("source_metadata->>opportunity_id", opportunity_id)
            .order("years_experience", desc=True, nullsfirst=False)
            .limit(20)
            .execute()
        )
        rows = resp.data or []

        candidates = []
        for r in rows:
            first = r.get("first_name") or ""
            last = r.get("last_name") or ""
            name = (f"{first} {last}").strip() or None
            candidates.append({
                "id": r.get("id"),
                "name": name,
                "headline": r.get("headline"),
                "location": r.get("location"),
                "years_experience": r.get("years_experience"),
                "approved": r.get("approved"),
                "source_metadata": r.get("source_metadata") or {},
            })

        return jsonify(candidates), 200
    except Exception as e:
        print(f"❌ /roles/{opportunity_id}/sourced-candidates error: {e}")
        return bad(str(e), 500)
