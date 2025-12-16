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
                    "This is expected - interactions remain as historical records."
                )
                print("  ⚠️  Cannot delete auth user: interactions are append-only")
            else:
                deletion_results["auth_user_deleted"] = False
                deletion_results["errors"].append(f"Auth user deletion failed: {response.status_code} - {error_msg}")
                print(f"  ⚠️  Could not delete auth user: {response.status_code} - {error_msg}")
        
        deletion_results["success"] = len(deletion_results["errors"]) == 0
        deletion_results["message"] = f"User {target_user_id} deletion completed"
        
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

@onboarding_bp.route("/onboarding/status", methods=["POST"])
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
