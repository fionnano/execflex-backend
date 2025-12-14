"""
Onboarding service for initializing application state for new identities.
Handles people_profiles, user_preferences, role_assignments, and outbound onboarding calls.
"""
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from config.clients import supabase_client, twilio_client
from config.app_config import TWILIO_PHONE_NUMBER
from utils.response_helpers import ok, bad


# Hardcoded destination for onboarding calls
ONBOARDING_DESTINATION_PHONE = "+447463212071"


def initialize_user_onboarding(user_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Initialize onboarding for a new user (called by database trigger or admin).
    Note: This is a convenience wrapper. The database trigger handles full onboarding
    including people_profiles, user_preferences, and role_assignments.
    
    This function primarily enqueues the outbound call job if needed.
    
    Args:
        user_id: User ID (required for production, nullable for testing)
    
    Returns:
        Dict with job_id, thread_id, interaction_id, status
    """
    if not supabase_client:
        raise RuntimeError("Supabase client not available")
    
    try:
        # Create dedupe key to prevent duplicate jobs within 1 hour
        dedupe_key = f"qualification-{user_id or 'test'}-{datetime.utcnow().strftime('%Y%m%d%H')}"
        
        # Create thread for this qualification call
        from datetime import timezone
        now_iso = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()
        
        thread_data = {
            "primary_user_id": user_id,  # Required by threads table
            "subject": "Qualification call",
            "status": "open",
            "active": True,
            "created_at": now_iso,
            "updated_at": now_iso
        }
        thread_resp = supabase_client.table("threads").insert(thread_data).execute()
        thread_id = thread_resp.data[0]["id"] if thread_resp.data else None
        
        # Create interaction record (will be updated when call starts)
        # Note: interactions table doesn't have 'status' - use started_at/ended_at instead
        interaction_data = {
            "thread_id": thread_id,
            "user_id": user_id,  # Set user_id for tracking
            "channel": "voice",
            "direction": "outbound",
            "provider": "twilio",
            "started_at": now_iso,  # Will be updated when call actually starts
            "created_at": now_iso
        }
        interaction_resp = supabase_client.table("interactions").insert(interaction_data).execute()
        interaction_id = interaction_resp.data[0]["id"] if interaction_resp.data else None
        
        # Create outbound call job
        job_data = {
            "user_id": user_id,  # Set user_id for tracking
            "phone_e164": ONBOARDING_DESTINATION_PHONE,
            "status": "queued",
            "thread_id": thread_id,
            "interaction_id": interaction_id,
            "dedupe_key": dedupe_key,
            "artifacts": {
                "call_type": "qualification",
                "created_at": now_iso
            },
            "created_at": now_iso,
            "updated_at": now_iso
        }
        
        # Try to insert job (idempotency: dedupe_key prevents duplicates within same hour)
        try:
            job_resp = supabase_client.table("outbound_call_jobs").insert(job_data).execute()
            job_id = job_resp.data[0]["id"] if job_resp.data else None
        except Exception as insert_error:
            # If duplicate (idempotency constraint), fetch existing job
            error_str = str(insert_error)
            if "duplicate key" in error_str.lower() or "23505" in error_str:
                print(f"ℹ️  Job already exists for this user/hour (idempotency), fetching existing job...")
                existing_job = supabase_client.table("outbound_call_jobs")\
                    .select("*")\
                    .eq("user_id", user_id)\
                    .eq("dedupe_key", dedupe_key)\
                    .limit(1)\
                    .execute()
                
                if existing_job.data and len(existing_job.data) > 0:
                    job_id = existing_job.data[0]["id"]
                    thread_id = existing_job.data[0].get("thread_id")
                    interaction_id = existing_job.data[0].get("interaction_id")
                    print(f"✅ Using existing job: {job_id}")
                else:
                    raise insert_error
            else:
                raise insert_error
        
        return {
            "job_id": job_id,
            "thread_id": thread_id,
            "interaction_id": interaction_id,
            "status": "queued"
        }
    except Exception as e:
        print(f"❌ Error initializing user onboarding: {e}")
        raise


def process_queued_jobs(limit: int = 10) -> int:
    """
    Process queued outbound call jobs.
    Picks up jobs with status='queued' and initiates Twilio calls.
    
    Args:
        limit: Maximum number of jobs to process in this run
    
    Returns:
        Number of jobs processed
    """
    if not supabase_client or not twilio_client:
        print("⚠️ Supabase or Twilio client not available")
        return 0
    
    try:
        # Fetch queued jobs (ready to run now or in the past)
        now = datetime.utcnow()
        # Use Supabase query builder - fetch queued jobs where next_run_at is null or in the past
        jobs_resp = supabase_client.table("outbound_call_jobs")\
            .select("*")\
            .eq("status", "queued")\
            .order("created_at", desc=False)\
            .limit(limit)\
            .execute()
        
        # Filter jobs that are ready to run (next_run_at is null or in the past)
        jobs = []
        for job in (jobs_resp.data or []):
            next_run = job.get("next_run_at")
            if not next_run:
                jobs.append(job)
            else:
                # Parse next_run_at and check if it's in the past
                try:
                    next_run_dt = datetime.fromisoformat(next_run.replace('Z', '+00:00'))
                    if next_run_dt.replace(tzinfo=None) <= now:
                        jobs.append(job)
                except (ValueError, AttributeError):
                    # If parsing fails, include the job anyway
                    jobs.append(job)
        
        processed = 0
        
        for job in jobs:
            try:
                job_id = job["id"]
                user_id = job.get("user_id")
                phone = job["phone_e164"]
                thread_id = job.get("thread_id")
                interaction_id = job.get("interaction_id")
                
                # Update job to running
                from datetime import timezone
                now_iso = now.replace(tzinfo=timezone.utc).isoformat()
                supabase_client.table("outbound_call_jobs")\
                    .update({
                        "status": "running",
                        "attempts": job.get("attempts", 0) + 1,
                        "updated_at": now_iso
                    })\
                    .eq("id", job_id)\
                    .execute()
                
                # Initiate Twilio call
                # Construct URL manually (url_for requires app context which we don't have in worker)
                base_url = os.getenv("API_BASE_URL", os.getenv("VITE_FLASK_API_URL", "https://api.execflex.ai"))
                twiml_url = f"{base_url}/voice/onboarding/intro?job_id={job_id}"
                
                call = twilio_client.calls.create(
                    to=phone,
                    from_=TWILIO_PHONE_NUMBER,
                    url=twiml_url,
                    status_callback=f"{base_url}/voice/onboarding/status",
                    status_callback_event=["initiated", "ringing", "answered", "completed", "failed", "busy", "no-answer"],
                    status_callback_method="POST"
                )
                
                call_sid = call.sid
                
                # Update job with call SID and store interaction info in artifacts
                # Note: interactions are append-only, so we can't update them
                # Store call info in job artifacts instead
                job_artifacts = job.get("artifacts", {}) or {}
                job_artifacts.update({
                    "call_initiated_at": now_iso,
                    "twilio_call_sid": call_sid,
                    "interaction_id": interaction_id
                })
                
                supabase_client.table("outbound_call_jobs")\
                    .update({
                        "twilio_call_sid": call_sid,
                        "artifacts": job_artifacts,
                        "updated_at": now_iso
                    })\
                    .eq("id", job_id)\
                    .execute()
                
                # Note: We don't update the interaction because interactions are append-only
                # The interaction was created at enqueue time with initial state
                # Call status will be tracked via the job record and status callbacks
                
                print(f"✅ Initiated onboarding call: job_id={job_id}, call_sid={call_sid}, phone={phone}")
                processed += 1
                
            except Exception as e:
                # Mark job as failed and set retry
                error_msg = str(e)
                print(f"❌ Error processing job {job.get('id')}: {error_msg}")
                
                attempts = job.get("attempts", 0) + 1
                backoff_minutes = min(2 ** attempts, 60)  # Exponential backoff, max 60 min
                from datetime import timezone
                next_run = (now + timedelta(minutes=backoff_minutes)).replace(tzinfo=timezone.utc).isoformat()
                now_iso = now.replace(tzinfo=timezone.utc).isoformat()
                
                supabase_client.table("outbound_call_jobs")\
                    .update({
                        "status": "failed" if attempts >= 3 else "queued",
                        "last_error": error_msg,
                        "next_run_at": next_run if attempts < 3 else None,
                        "updated_at": now_iso
                    })\
                    .eq("id", job["id"])\
                    .execute()
        
        return processed
        
    except Exception as e:
        print(f"❌ Error processing queued jobs: {e}")
        import traceback
        traceback.print_exc()
        return 0
