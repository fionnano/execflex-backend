"""
Onboarding service routes for outbound call management.
Handles onboarding calls triggered after user signup.
"""
import os
import requests
from flask import request, Response, jsonify
from routes import onboarding_bp
from utils.response_helpers import ok, bad
from utils.auth_helpers import require_admin, get_authenticated_user_id
from services.onboarding_service import initialize_user_onboarding, process_queued_jobs
from config.clients import twilio_client, supabase_client
from config.app_config import TWILIO_PHONE_NUMBER, SUPABASE_URL, SUPABASE_KEY


@onboarding_bp.route("/enqueue", methods=["POST"])
@require_admin
def enqueue_call():
    """
    Manually trigger onboarding for a user (admin-only).
    
    **ADMIN ONLY**: This endpoint requires authentication AND admin role.
    Used for manual/admin triggers, testing, or re-triggering onboarding.
    
    **Note:** Automatic onboarding is handled by database trigger on signup,
    so this endpoint is primarily for admin operations or edge cases.
    
    Headers:
        Authorization: Bearer <supabase_jwt_token>
    
    Body (JSON, required): { "user_id": "uuid" }
        - The user_id to initialize onboarding for (can be any user, not just the authenticated admin)
    """
    try:
        # Get authenticated admin user (for logging/audit)
        admin_user_id = request.environ.get('authenticated_user_id')
        
        data = request.get_json(silent=True) or {}
        target_user_id = data.get("user_id")
        
        if not target_user_id:
            return bad("user_id is required", 400)
        
        print(f"🔐 Admin {admin_user_id} triggering onboarding for user {target_user_id}")
        
        # Initialize onboarding (will check if already done by trigger)
        result = initialize_user_onboarding(user_id=target_user_id)
        return ok(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"⚠️ Warning: Failed to initialize onboarding: {str(e)}")
        return ok({"status": "onboarding_failed", "error": str(e)})


@onboarding_bp.route("/set-admin", methods=["POST"])
@require_admin
def set_admin():
    """
    Set a user as admin (admin-only).
    
    **ADMIN ONLY**: This endpoint requires authentication AND admin role.
    Adds 'admin' role to the specified user in role_assignments table.
    
    Headers:
        Authorization: Bearer <supabase_jwt_token>
    
    Body (JSON, required): { "user_id": "uuid" }
        - The user_id to grant admin role to
    """
    try:
        # Get authenticated admin user (for logging/audit)
        admin_user_id = request.environ.get('authenticated_user_id')
        
        data = request.get_json(silent=True) or {}
        target_user_id = data.get("user_id")
        
        if not target_user_id:
            return bad("user_id is required", 400)
        
        print(f"🔐 Admin {admin_user_id} setting admin role for user {target_user_id}")
        
        # Check if user already has admin role
        existing = supabase_client.table("role_assignments")\
            .select("id")\
            .eq("user_id", target_user_id)\
            .eq("role", "admin")\
            .limit(1)\
            .execute()
        
        if existing.data and len(existing.data) > 0:
            return ok({
                "message": "User already has admin role",
                "user_id": target_user_id,
                "already_admin": True
            })
        
        # Insert admin role assignment
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        
        result = supabase_client.table("role_assignments")\
            .insert({
                "user_id": target_user_id,
                "role": "admin",
                "confidence": 1.0,
                "evidence": {
                    "source": "manual",
                    "granted_by": admin_user_id,
                    "granted_at": now_iso
                },
                "created_at": now_iso
            })\
            .execute()
        
        if result.data:
            print(f"✅ Admin role granted to user {target_user_id}")
            return ok({
                "message": "Admin role granted successfully",
                "user_id": target_user_id,
                "role_assignment_id": result.data[0]["id"]
            })
        else:
            return bad("Failed to grant admin role", 500)
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Error setting admin role: {e}")
        return bad(f"Error setting admin role: {str(e)}", 500)


@onboarding_bp.route("/set-user-mode", methods=["POST"])
@require_admin
def set_user_mode():
    """
    Set a user's mode (talent|hirer) (admin-only).
    
    **ADMIN ONLY**: This endpoint requires authentication AND admin role.
    Updates:
    - user_preferences.default_mode + user_preferences.last_mode
    - role_assignments (talent/hirer) so the voice qualification flow stays consistent
    
    Body (JSON, required): { "user_id": "uuid", "mode": "talent" | "hirer" }
    """
    try:
        from datetime import datetime, timezone
        admin_user_id = request.environ.get('authenticated_user_id')

        data = request.get_json(silent=True) or {}
        target_user_id = data.get("user_id")
        mode = (data.get("mode") or "").strip().lower()

        if not target_user_id:
            return bad("user_id is required", 400)
        if mode not in ("talent", "hirer"):
            return bad("mode must be 'talent' or 'hirer'", 400)

        print(f"🔐 Admin {admin_user_id} setting user_mode={mode} for user {target_user_id}")

        # Upsert user_preferences row for the target user
        prefs_existing = supabase_client.table("user_preferences")\
            .select("user_id")\
            .eq("user_id", target_user_id)\
            .limit(1)\
            .execute()

        if prefs_existing.data:
            supabase_client.table("user_preferences")\
                .update({"default_mode": mode, "last_mode": mode})\
                .eq("user_id", target_user_id)\
                .execute()
        else:
            supabase_client.table("user_preferences")\
                .insert({"user_id": target_user_id, "default_mode": mode, "last_mode": mode})\
                .execute()

        # Keep role_assignments aligned (only one of talent/hirer at a time)
        now_iso = datetime.now(timezone.utc).isoformat()
        role_existing = supabase_client.table("role_assignments")\
            .select("id, role")\
            .eq("user_id", target_user_id)\
            .in_("role", ["talent", "hirer"])\
            .order("confidence", desc=True)\
            .limit(1)\
            .execute()

        updated_role_id = None
        if role_existing.data:
            updated_role_id = role_existing.data[0]["id"]
            supabase_client.table("role_assignments")\
                .update({
                    "role": mode,
                    "confidence": 1.0,
                    "evidence": {
                        "source": "manual",
                        "set_by": admin_user_id,
                        "set_at": now_iso,
                        "type": "mode_change"
                    }
                })\
                .eq("id", updated_role_id)\
                .execute()

            # Remove any other stale talent/hirer rows to avoid ambiguity in downstream lookups
            supabase_client.table("role_assignments")\
                .delete()\
                .eq("user_id", target_user_id)\
                .in_("role", ["talent", "hirer"])\
                .neq("id", updated_role_id)\
                .execute()
        else:
            inserted = supabase_client.table("role_assignments")\
                .insert({
                    "user_id": target_user_id,
                    "role": mode,
                    "confidence": 1.0,
                    "evidence": {
                        "source": "manual",
                        "set_by": admin_user_id,
                        "set_at": now_iso,
                        "type": "mode_change"
                    },
                    "created_at": now_iso
                })\
                .execute()
            if inserted.data:
                updated_role_id = inserted.data[0].get("id")

        return ok({
            "message": "User mode updated",
            "user_id": target_user_id,
            "mode": mode,
            "role_assignment_id": updated_role_id
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Error setting user mode: {e}")
        return bad(f"Error setting user mode: {str(e)}", 500)


@onboarding_bp.route("/conversations", methods=["GET"])
@require_admin
def get_conversations():
    """
    Get list of outbound qualifying conversations (admin-only).
    
    **ADMIN ONLY**: This endpoint requires authentication AND admin role.
    Returns all outbound call jobs with user information and conversation summaries.
    
    Headers:
        Authorization: Bearer <supabase_jwt_token>
    
    Query Parameters (optional):
        - status: Filter by status (queued, running, succeeded, failed)
        - limit: Limit number of results (default: 100)
        - offset: Pagination offset (default: 0)
    
    Returns:
        {
            "conversations": [
                {
                    "id": "uuid",
                    "phone_e164": "+447700900001",
                    "status": "completed",
                    "attempts": 1,
                    "created_at": "2024-12-15T10:30:00Z",
                    "updated_at": "2024-12-15T10:35:00Z",
                    "next_run_at": null,
                    "last_error": null,
                    "twilio_call_sid": "CA1234567890abcdef",
                    "summary": "Candidate interested in CFO roles...",
                    "user_name": "Sarah Mitchell"
                },
                ...
            ],
            "total": 50
        }
    """
    try:
        # Get query parameters
        status_filter = request.args.get("status")
        limit = int(request.args.get("limit", 100))
        offset = int(request.args.get("offset", 0))
        
        # Build base query
        query = supabase_client.table("outbound_call_jobs").select(
            "id, phone_e164, status, attempts, created_at, updated_at, next_run_at, last_error, twilio_call_sid, interaction_id, user_id",
            count="exact"
        )
        
        # Apply filters
        if status_filter:
            # Map frontend status to database status
            status_map = {
                "pending": "queued",
                "in_progress": "running",
                "completed": "succeeded",
                "failed": "failed"
            }
            db_status = status_map.get(status_filter, status_filter)
            query = query.eq("status", db_status)
        
        # Apply pagination and ordering
        query = query.order("created_at", desc=True).limit(limit).offset(offset)
        
        # Execute query
        result = query.execute()
        
        # Get all user_ids and interaction_ids for batch fetching
        user_ids = [job["user_id"] for job in result.data if job.get("user_id")]
        interaction_ids = [job["interaction_id"] for job in result.data if job.get("interaction_id")]
        
        # Batch fetch user profiles (query each user individually if .in_() doesn't work)
        profiles_map = {}
        if user_ids:
            try:
                # Try using .in_() method (Supabase Python client supports this)
                profiles_result = supabase_client.table("people_profiles").select("user_id, first_name, last_name").in_("user_id", user_ids).execute()
                for profile in (profiles_result.data or []):
                    profiles_map[profile["user_id"]] = profile
            except AttributeError:
                # Fallback: query individually
                for user_id in user_ids:
                    try:
                        profile_result = supabase_client.table("people_profiles").select("user_id, first_name, last_name").eq("user_id", user_id).limit(1).execute()
                        if profile_result.data:
                            profiles_map[user_id] = profile_result.data[0]
                    except Exception:
                        continue
        
        # Batch fetch interaction summaries
        summaries_map = {}
        if interaction_ids:
            try:
                # Try using .in_() method
                interactions_result = supabase_client.table("interactions").select("id, summary_text").in_("id", interaction_ids).execute()
                for interaction in (interactions_result.data or []):
                    summaries_map[interaction["id"]] = interaction.get("summary_text")
            except AttributeError:
                # Fallback: query individually
                for interaction_id in interaction_ids:
                    try:
                        interaction_result = supabase_client.table("interactions").select("id, summary_text").eq("id", interaction_id).limit(1).execute()
                        if interaction_result.data:
                            summaries_map[interaction_id] = interaction_result.data[0].get("summary_text")
                    except Exception:
                        continue
        
        # Transform data to match frontend format
        conversations = []
        for job in result.data:
            # Get user name from people_profiles
            user_name = None
            if job.get("user_id") and job["user_id"] in profiles_map:
                profile = profiles_map[job["user_id"]]
                first_name = profile.get("first_name") or ""
                last_name = profile.get("last_name") or ""
                if first_name or last_name:
                    user_name = f"{first_name} {last_name}".strip()
            
            # Get summary from interactions
            summary = None
            if job.get("interaction_id") and job["interaction_id"] in summaries_map:
                summary = summaries_map[job["interaction_id"]]
            
            # Map database status to frontend status
            status_map = {
                "queued": "pending",
                "running": "in_progress",
                "succeeded": "completed",
                "failed": "failed"
            }
            frontend_status = status_map.get(job["status"], job["status"])
            
            conversations.append({
                "id": job["id"],
                "phone_e164": job["phone_e164"],
                "status": frontend_status,
                "attempts": job["attempts"],
                "created_at": job["created_at"],
                "updated_at": job["updated_at"],
                "next_run_at": job.get("next_run_at"),
                "last_error": job.get("last_error"),
                "twilio_call_sid": job.get("twilio_call_sid"),
                "summary": summary,
                "user_name": user_name
            })
        
        return ok({
            "conversations": conversations,
            "total": result.count or len(conversations)
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Error fetching conversations: {str(e)}")
        return bad(f"Failed to fetch conversations: {str(e)}", 500)


@onboarding_bp.route("/conversations/<conversation_id>", methods=["GET"])
@require_admin
def get_conversation_details(conversation_id: str):
    """
    Get detailed conversation data for a specific conversation (admin-only).
    
    **ADMIN ONLY**: This endpoint requires authentication AND admin role.
    Returns conversation turns, transcript, and full interaction details.
    
    Headers:
        Authorization: Bearer <supabase_jwt_token>
    
    Returns:
        {
            "conversation": {
                "id": "uuid",
                "phone_e164": "+447700900001",
                "status": "completed",
                "interaction_id": "uuid",
                "transcript": "Full transcript text...",
                "summary": "Summary text...",
                "turns": [
                    {
                        "speaker": "assistant",
                        "text": "Hello, this is ExecFlex...",
                        "created_at": "2024-12-15T10:30:00Z",
                        "turn_sequence": 1
                    },
                    ...
                ]
            }
        }
    """
    try:
        # Get the job
        job_result = supabase_client.table("outbound_call_jobs")\
            .select("id, phone_e164, status, interaction_id, user_id, twilio_call_sid, created_at, updated_at")\
            .eq("id", conversation_id)\
            .limit(1)\
            .execute()
        
        if not job_result.data or len(job_result.data) == 0:
            return bad("Conversation not found", 404)
        
        job = job_result.data[0]
        interaction_id = job.get("interaction_id")
        
        # Get interaction details (transcript, summary)
        interaction_data = {}
        if interaction_id:
            try:
                interaction_result = supabase_client.table("interactions")\
                    .select("id, transcript_text, summary_text, started_at, ended_at, artifacts")\
                    .eq("id", interaction_id)\
                    .limit(1)\
                    .execute()
                
                if interaction_result.data:
                    interaction_data = interaction_result.data[0]
            except Exception as e:
                print(f"⚠️ Error fetching interaction: {e}")
        
        # Get conversation turns (if interaction_turns table exists)
        turns = []
        if interaction_id:
            try:
                # Check if interaction_turns table exists by trying to query it
                turns_result = supabase_client.table("interaction_turns")\
                    .select("speaker, text, created_at, turn_sequence, artifacts_json")\
                    .eq("interaction_id", interaction_id)\
                    .order("turn_sequence", desc=False)\
                    .execute()
                
                if turns_result.data:
                    turns = [
                        {
                            "speaker": turn.get("speaker"),
                            "text": turn.get("text"),
                            "created_at": turn.get("created_at"),
                            "turn_sequence": turn.get("turn_sequence"),
                            "artifacts": turn.get("artifacts_json")
                        }
                        for turn in turns_result.data
                    ]
            except Exception as e:
                # Table might not exist or error - that's okay, we'll use transcript
                print(f"⚠️ Could not fetch turns (table may not exist): {e}")
        
        # Map database status to frontend status
        status_map = {
            "queued": "pending",
            "running": "in_progress",
            "succeeded": "completed",
            "failed": "failed"
        }
        frontend_status = status_map.get(job["status"], job["status"])
        
        return ok({
            "conversation": {
                "id": job["id"],
                "phone_e164": job["phone_e164"],
                "status": frontend_status,
                "interaction_id": interaction_id,
                "twilio_call_sid": job.get("twilio_call_sid"),
                "created_at": job["created_at"],
                "updated_at": job["updated_at"],
                "transcript": interaction_data.get("transcript_text"),
                "summary": interaction_data.get("summary_text"),
                "turns": turns,
                "started_at": interaction_data.get("started_at"),
                "ended_at": interaction_data.get("ended_at"),
                "artifacts": interaction_data.get("artifacts")
            }
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Error fetching conversation details: {str(e)}")
        return bad(f"Failed to fetch conversation details: {str(e)}", 500)


@onboarding_bp.route("/list-users", methods=["GET"])
@require_admin
def list_users():
    """
    Get list of all users with their roles (admin-only).
    
    **ADMIN ONLY**: This endpoint requires authentication AND admin role.
    Returns users from auth.users who have entries in people_profiles (i.e., have completed onboarding).
    Only shows users who have profiles in our system, not just auth.users entries.
    Includes their associated roles from role_assignments.
    
    Headers:
        Authorization: Bearer <supabase_jwt_token>
    
    Query Parameters (optional):
        - limit: Limit number of results (default: 100)
        - offset: Pagination offset (default: 0)
    
    Returns:
        {
            "users": [
                {
                    "id": "uuid",
                    "email": "user@example.com",
                    "phone": "+1234567890",
                    "created_at": "2024-12-15T10:30:00Z",
                    "last_sign_in_at": "2024-12-16T10:30:00Z",
                    "roles": ["hirer", "admin"]
                },
                ...
            ],
            "total": 50
        }
    """
    try:
        # Get query parameters
        limit = int(request.args.get("limit", 100))
        offset = int(request.args.get("offset", 0))
        
        # Use Supabase Admin API to list users
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json"
        }
        
        list_url = f"{SUPABASE_URL}/auth/v1/admin/users"
        params = {"per_page": limit, "page": (offset // limit) + 1 if limit > 0 else 1}
        
        response = requests.get(list_url, headers=headers, params=params)
        
        if response.status_code != 200:
            return bad(f"Error querying auth users: {response.status_code}", 500)
        
        auth_users = response.json().get("users", [])
        
        # Get all user IDs to check which ones have people_profiles
        user_ids = [user["id"] for user in auth_users]
        
        # Filter to only users who have entries in people_profiles
        # This ensures we only show users who have actually completed onboarding
        # or have profiles in our system (not just auth.users entries)
        users_with_profiles = set()
        if user_ids:
            try:
                # Batch fetch user_ids that have people_profiles
                profiles_result = supabase_client.table("people_profiles")\
                    .select("user_id")\
                    .in_("user_id", user_ids)\
                    .execute()
                
                users_with_profiles = {profile["user_id"] for profile in (profiles_result.data or [])}
            except AttributeError:
                # Fallback: query individually
                for user_id in user_ids:
                    try:
                        profile_result = supabase_client.table("people_profiles")\
                            .select("user_id")\
                            .eq("user_id", user_id)\
                            .limit(1)\
                            .execute()
                        if profile_result.data:
                            users_with_profiles.add(user_id)
                    except Exception:
                        continue
        
        # Filter auth_users to only those with profiles
        filtered_auth_users = [user for user in auth_users if user["id"] in users_with_profiles]
        
        # Get all filtered user IDs to fetch roles
        filtered_user_ids = [user["id"] for user in filtered_auth_users]
        
        # Fetch roles for filtered users
        roles_map = {}
        if filtered_user_ids:
            try:
                # Try batch fetch with .in_()
                roles_result = supabase_client.table("role_assignments")\
                    .select("user_id, role")\
                    .in_("user_id", filtered_user_ids)\
                    .execute()
                
                for role_assignment in (roles_result.data or []):
                    user_id = role_assignment["user_id"]
                    if user_id not in roles_map:
                        roles_map[user_id] = []
                    roles_map[user_id].append(role_assignment["role"])
            except AttributeError:
                # Fallback: query individually
                for user_id in filtered_user_ids:
                    try:
                        role_result = supabase_client.table("role_assignments")\
                            .select("role")\
                            .eq("user_id", user_id)\
                            .execute()
                        if role_result.data:
                            roles_map[user_id] = [r["role"] for r in role_result.data]
                    except Exception:
                        continue

        # Fetch user mode from user_preferences for filtered users (last_mode > default_mode)
        modes_map = {}
        if filtered_user_ids:
            try:
                prefs_result = supabase_client.table("user_preferences")\
                    .select("user_id, last_mode, default_mode")\
                    .in_("user_id", filtered_user_ids)\
                    .execute()

                for row in (prefs_result.data or []):
                    user_id = row.get("user_id")
                    candidate = (row.get("last_mode") or row.get("default_mode") or "")
                    candidate = str(candidate).strip().lower()
                    if user_id and candidate in ("talent", "hirer"):
                        modes_map[user_id] = candidate
            except AttributeError:
                # Fallback: query individually
                for user_id in filtered_user_ids:
                    try:
                        prefs_result = supabase_client.table("user_preferences")\
                            .select("last_mode, default_mode")\
                            .eq("user_id", user_id)\
                            .limit(1)\
                            .execute()
                        if prefs_result.data:
                            row = prefs_result.data[0] or {}
                            candidate = (row.get("last_mode") or row.get("default_mode") or "")
                            candidate = str(candidate).strip().lower()
                            if candidate in ("talent", "hirer"):
                                modes_map[user_id] = candidate
                    except Exception:
                        continue
        
        # Transform data to match frontend format
        users = []
        for auth_user in filtered_auth_users:
            user_id = auth_user["id"]
            roles = roles_map.get(user_id, []) or []
            # Fallback to role_assignments if user_preferences missing
            fallback_mode = None
            if "hirer" in roles:
                fallback_mode = "hirer"
            elif "talent" in roles:
                fallback_mode = "talent"
            users.append({
                "id": user_id,
                "email": auth_user.get("email"),
                "phone": auth_user.get("phone"),
                "created_at": auth_user.get("created_at"),
                "last_sign_in_at": auth_user.get("last_sign_in_at"),
                "roles": roles,
                "user_mode": modes_map.get(user_id) or fallback_mode
            })
        
        return ok({
            "users": users,
            "total": len(users)  # Note: Supabase Admin API doesn't return total count easily
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Error listing users: {str(e)}")
        return bad(f"Failed to list users: {str(e)}", 500)


@onboarding_bp.route("/delete-user", methods=["POST"])
@require_admin
def delete_user():
    """
    Delete a user and all associated data (admin-only).
    
    **ADMIN ONLY**: This endpoint requires authentication AND admin role.
    Performs comprehensive cleanup of all user-related data including:
    - outbound_call_jobs, thread_participants, threads, organization_members
    - opportunities, match_suggestions, channel_identities
    - role_assignments, user_preferences, people_profiles
    - organizations (sets created_by_user_id to NULL)
    - auth.users (via Supabase Admin API)
    
    Note: Interactions are append-only (event sourcing) and remain as historical records.
    
    Headers:
        Authorization: Bearer <supabase_jwt_token>
    
    Body (JSON, required): { "user_id": "uuid" } OR { "phone": "+1234567890" }
        - user_id: UUID of the user to delete
        - phone: Phone number (E.164 format) to find and delete user by
    
    Returns:
        JSON response with deletion status and details
    """
    try:
        # Get authenticated admin user (for logging/audit)
        admin_user_id = request.environ.get('authenticated_user_id')
        
        data = request.get_json(silent=True) or {}
        target_user_id = data.get("user_id")
        phone = data.get("phone")
        
        if not target_user_id and not phone:
            return bad("Either user_id or phone is required", 400)
        
        # If phone provided, find user_id first
        if phone and not target_user_id:
            print(f"🔐 Admin {admin_user_id} searching for user by phone: {phone}")
            
            # Search for user by phone in auth.users (requires admin API)
            headers = {
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json"
            }
            
            list_url = f"{SUPABASE_URL}/auth/v1/admin/users"
            response = requests.get(list_url, headers=headers, params={"phone": phone})
            
            if response.status_code != 200:
                return bad(f"Error querying auth users: {response.status_code}", 500)
            
            users = response.json().get("users", [])
            if not users:
                return bad(f"User with phone {phone} not found", 404)
            
            target_user_id = users[0]["id"]
            print(f"  Found user ID: {target_user_id}")
        
        if not target_user_id:
            return bad("Could not determine user_id", 400)
        
        print(f"🔐 Admin {admin_user_id} deleting user {target_user_id}")
        
        deletion_results = {
            "user_id": target_user_id,
            "deleted_tables": [],
            "errors": [],
            "warnings": []
        }
        
        # Delete from related tables (order matters due to foreign keys)
        tables_to_delete = [
            ("outbound_call_jobs", "user_id"),
            ("thread_participants", "user_id"),
            ("organization_members", "user_id"),
            ("opportunities", "created_by_user_id"),
            ("match_suggestions", "suggested_user_id"),
            ("channel_identities", "user_id"),
            ("role_assignments", "user_id"),
            ("user_preferences", "user_id"),
            ("people_profiles", "user_id"),
        ]
        
        for table_name, column_name in tables_to_delete:
            try:
                result = supabase_client.table(table_name).delete().eq(column_name, target_user_id).execute()
                deletion_results["deleted_tables"].append(table_name)
                print(f"  ✓ Deleted {table_name}")
            except Exception as e:
                error_msg = str(e)
                if "does not exist" in error_msg.lower():
                    pass  # Table doesn't exist, skip silently
                else:
                    deletion_results["errors"].append(f"{table_name}: {error_msg}")
                    print(f"  ⚠️  Error deleting {table_name}: {e}")
        
        # Mark threads as inactive (interactions are append-only)
        try:
            supabase_client.table("threads").update({"active": False}).or_(
                f"primary_user_id.eq.{target_user_id},owner_user_id.eq.{target_user_id}"
            ).execute()
            deletion_results["deleted_tables"].append("threads (marked inactive)")
            print("  ✓ Marked threads as inactive")
        except Exception as e:
            deletion_results["warnings"].append(f"threads: {e}")
            print(f"  ⚠️  Error updating threads: {e}")
        
        deletion_results["warnings"].append("Interactions are append-only and remain as historical records")
        print("  ℹ️  Interactions are append-only and remain as historical records")
        
        # Update organizations (set created_by_user_id to NULL)
        try:
            supabase_client.table("organizations").update({"created_by_user_id": None}).eq(
                "created_by_user_id", target_user_id
            ).execute()
            deletion_results["deleted_tables"].append("organizations (updated)")
            print("  ✓ Updated organizations (set created_by_user_id to NULL)")
        except Exception as e:
            deletion_results["warnings"].append(f"organizations: {e}")
            print(f"  ⚠️  Error updating organizations: {e}")
        
        # Finally, delete auth user (requires admin API)
        # Note: Even if this fails, we've deleted all application data, so the user
        # won't appear in list-users (which filters by people_profiles)
        print("\nDeleting auth user...")
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json"
        }
        delete_url = f"{SUPABASE_URL}/auth/v1/admin/users/{target_user_id}"
        response = requests.delete(delete_url, headers=headers)
        
        if response.status_code == 200:
            deletion_results["auth_user_deleted"] = True
            print("  ✓ Deleted auth user")
        elif response.status_code == 404:
            deletion_results["auth_user_deleted"] = False
            deletion_results["warnings"].append("Auth user not found (may have been already deleted)")
            print("  ⚠️  Auth user not found (may have been already deleted)")
        else:
            error_response = response.json() if response.text else {}
            error_msg = error_response.get("message", response.text)
            
            if "append-only" in error_msg.lower() or "interactions" in error_msg.lower():
                deletion_results["auth_user_deleted"] = False
                deletion_results["warnings"].append(
                    "Cannot delete auth user: interactions are append-only (event sourcing). "
                    "This is expected - interactions remain as historical records. "
                    "User will not appear in admin list (filtered by people_profiles)."
                )
                print("  ⚠️  Cannot delete auth user: interactions are append-only")
            else:
                deletion_results["auth_user_deleted"] = False
                deletion_results["warnings"].append(
                    f"Auth user deletion failed: {response.status_code} - {error_msg}. "
                    "User will not appear in admin list (filtered by people_profiles)."
                )
                print(f"  ⚠️  Could not delete auth user: {response.status_code} - {error_msg}")
                print(f"  Response body: {response.text}")
        
        # Consider deletion successful if all application data is deleted
        # Even if auth.users deletion fails, user won't appear in admin list
        deletion_results["success"] = len(deletion_results["errors"]) == 0
        deletion_results["message"] = (
            f"User {target_user_id} deletion completed. "
            "All application data removed. User will no longer appear in admin list."
        )
        
        return ok(deletion_results)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Error deleting user: {e}")
        return bad(f"Error deleting user: {str(e)}", 500)


# DEPRECATED ENDPOINTS REMOVED:
# All conversation handling is unified:
# - Outbound: /voice/qualify (handles entire outbound qualification conversation)
# - Status: /voice/status (handles all call status updates)
# The worker uses /voice/qualify and /voice/status directly

@onboarding_bp.route("/status", methods=["POST"])
def onboarding_status():
    """
    DEPRECATED: Twilio status callback webhook for onboarding calls.
    Use /voice/status instead.
    
    Kept for backward compatibility only.
    """
    call_sid = request.form.get("CallSid")
    call_status = request.form.get("CallStatus")  # queued, ringing, in-progress, completed, failed, busy, no-answer, canceled
    call_duration = request.form.get("CallDuration")  # seconds, only for completed
    from_number = request.form.get("From")
    to_number = request.form.get("To")
    
    if not call_sid:
        return Response("Missing CallSid", status=400), 400
    
    try:
        from config.clients import supabase_client
        from datetime import datetime
        
        if not supabase_client:
            print("⚠️ Supabase client not available for status update")
            return Response("OK", status=200), 200
        
        # Find job by call_sid
        job_resp = supabase_client.table("outbound_call_jobs")\
            .select("*")\
            .eq("twilio_call_sid", call_sid)\
            .limit(1)\
            .execute()
        
        if not job_resp.data:
            print(f"⚠️ No job found for call_sid: {call_sid}")
            return Response("OK", status=200), 200
        
        job = job_resp.data[0]
        job_id = job["id"]
        interaction_id = job.get("interaction_id")
        
        # Map Twilio status to job status
        status_map = {
            "completed": "succeeded",
            "failed": "failed",
            "busy": "failed",
            "no-answer": "failed",
            "canceled": "failed"
        }
        job_status = status_map.get(call_status, job.get("status", "running"))
        
        # Update job
        from datetime import timezone
        now_iso = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()
        update_data = {
            "status": job_status,
            "updated_at": now_iso,
            "artifacts": {
                **job.get("artifacts", {}),
                "call_status": call_status,
                "call_duration": call_duration,
                "from_number": from_number,
                "to_number": to_number,
                "status_updated_at": now_iso
            }
        }
        
        supabase_client.table("outbound_call_jobs")\
            .update(update_data)\
            .eq("id", job_id)\
            .execute()
        
        # Update interaction
        # Note: interactions table doesn't have 'status' - use ended_at to indicate completion
        if interaction_id:
            interaction_update = {
                "raw_payload": request.form.to_dict()
            }
            
            # Set ended_at if call completed or failed
            if call_status in ["completed", "failed", "busy", "no-answer", "canceled"]:
                interaction_update["ended_at"] = datetime.utcnow().isoformat()
            
            # Note: interactions are append-only, so we can't update them
            # Instead, we'll store the status in the job's artifacts
            # The interaction's ended_at will be set when the call completes
            # For now, we'll just update the raw_payload via a direct SQL call if needed
            # But since interactions are append-only, we should create a new interaction record
            # For MVP, we'll just update the job and leave interaction as-is
            print(f"ℹ️  Interaction {interaction_id} status: {call_status} (interactions are append-only)")
        
        print(f"✅ Updated onboarding call status: job_id={job_id}, call_sid={call_sid}, status={call_status}")
        return Response("OK", status=200), 200
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Error updating call status: {e}")
        # Return 200 to prevent Twilio retries
        return Response("OK", status=200), 200


@onboarding_bp.route("/process-jobs", methods=["POST"])
def process_jobs_endpoint():
    """
    Endpoint to trigger job processing (for HTTP-based cron/scheduled tasks).
    
    **Note:** This endpoint is optional. If using Render Background Workers,
    you can run `python -m workers.call_dispatcher` directly instead.
    
    Optional query param: limit (default 10)
    """
    # Optional: Add service secret protection here if needed
    limit = int(request.args.get("limit", 10))
    
    try:
        processed = process_queued_jobs(limit=limit)
        return ok({"processed": processed, "message": f"Processed {processed} jobs"})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return bad(f"Failed to process jobs: {str(e)}", 500)
