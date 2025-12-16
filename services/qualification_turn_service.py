"""
Service for managing qualification conversation turns and incremental DB updates.
"""
from typing import Dict, Optional, List, Any
from config.clients import supabase_client
from datetime import datetime, timezone
import uuid


def get_or_create_interaction_for_call(call_sid: str, job_id: Optional[str] = None) -> Optional[Dict]:
    """
    Get or create an interaction record for a Twilio call.
    
    Args:
        call_sid: Twilio CallSid
        job_id: Optional job ID to link to
        
    Returns:
        Interaction dict with id, thread_id, user_id, or None if failed
    """
    if not supabase_client:
        return None
    
    try:
        # Try to find existing interaction by provider_ref
        existing = supabase_client.table("interactions")\
            .select("id, thread_id, user_id")\
            .eq("provider", "twilio")\
            .eq("provider_ref", call_sid)\
            .limit(1)\
            .execute()
        
        if existing.data and len(existing.data) > 0:
            return existing.data[0]
        
        # If not found, try to get from job
        if job_id:
            job_resp = supabase_client.table("outbound_call_jobs")\
                .select("interaction_id, thread_id, user_id")\
                .eq("id", job_id)\
                .limit(1)\
                .execute()
            
            if job_resp.data and len(job_resp.data) > 0:
                job = job_resp.data[0]
                interaction_id = job.get("interaction_id")
                thread_id = job.get("thread_id")
                user_id = job.get("user_id")
                
                if interaction_id:
                    # Interaction already exists, return it
                    interaction_resp = supabase_client.table("interactions")\
                        .select("id, thread_id, user_id")\
                        .eq("id", interaction_id)\
                        .limit(1)\
                        .execute()
                    
                    if interaction_resp.data:
                        return interaction_resp.data[0]
                
                # Create new interaction
                if thread_id:
                    interaction_payload = {
                        "thread_id": thread_id,
                        "user_id": user_id,
                        "channel": "voice",
                        "direction": "outbound",
                        "provider": "twilio",
                        "provider_ref": call_sid,
                        "started_at": datetime.now(timezone.utc).isoformat()
                    }
                    
                    interaction_resp = supabase_client.table("interactions")\
                        .insert(interaction_payload)\
                        .execute()
                    
                    if interaction_resp.data:
                        # Update job with interaction_id
                        supabase_client.table("outbound_call_jobs")\
                            .update({"interaction_id": interaction_resp.data[0]["id"]})\
                            .eq("id", job_id)\
                            .execute()
                        
                        return interaction_resp.data[0]
        
        return None
        
    except Exception as e:
        print(f"⚠️ Failed to get/create interaction: {e}")
        import traceback
        traceback.print_exc()
        return None


def get_next_turn_sequence(interaction_id: str) -> int:
    """
    Get the next turn sequence number for an interaction.
    
    Args:
        interaction_id: Interaction UUID
        
    Returns:
        Next sequence number (1-based)
    """
    if not supabase_client:
        return 1
    
    try:
        result = supabase_client.table("interaction_turns")\
            .select("turn_sequence")\
            .eq("interaction_id", interaction_id)\
            .order("turn_sequence", desc=True)\
            .limit(1)\
            .execute()
        
        if result.data and len(result.data) > 0:
            return result.data[0].get("turn_sequence", 0) + 1
        
        return 1
    except Exception as e:
        print(f"⚠️ Failed to get next turn sequence: {e}")
        return 1


def save_turn(
    interaction_id: str,
    thread_id: Optional[str],
    speaker: str,
    text: str,
    turn_sequence: int,
    artifacts_json: Optional[Dict] = None,
    raw_payload: Optional[Dict] = None
) -> Optional[str]:
    """
    Save a conversation turn (append-only).
    
    Args:
        interaction_id: Interaction UUID
        thread_id: Thread UUID (optional)
        speaker: "user", "assistant", or "system"
        text: Turn text content
        turn_sequence: Sequential turn number
        artifacts_json: Optional structured data
        raw_payload: Optional raw provider payload
        
    Returns:
        Turn ID if successful, None otherwise
    """
    if not supabase_client:
        return None
    
    try:
        turn_payload = {
            "interaction_id": interaction_id,
            "thread_id": thread_id,
            "speaker": speaker,
            "text": text,
            "turn_sequence": turn_sequence,
            "artifacts_json": artifacts_json or {},
            "raw_payload": raw_payload or {}
        }
        
        result = supabase_client.table("interaction_turns")\
            .insert(turn_payload)\
            .execute()
        
        if result.data and len(result.data) > 0:
            return result.data[0].get("id")
        
        return None
    except Exception as e:
        # Check if it's a duplicate (idempotency)
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            print(f"ℹ️  Turn already exists (idempotency): {e}")
            # Try to return existing turn ID
            try:
                existing = supabase_client.table("interaction_turns")\
                    .select("id")\
                    .eq("interaction_id", interaction_id)\
                    .eq("turn_sequence", turn_sequence)\
                    .eq("speaker", speaker)\
                    .limit(1)\
                    .execute()
                
                if existing.data:
                    return existing.data[0].get("id")
            except:
                pass
        
        print(f"⚠️ Failed to save turn: {e}")
        import traceback
        traceback.print_exc()
        return None


def get_conversation_turns(interaction_id: str, limit: int = 20) -> List[Dict]:
    """
    Get conversation turns for an interaction (for OpenAI context).
    
    Args:
        interaction_id: Interaction UUID
        limit: Maximum number of turns to return (most recent)
        
    Returns:
        List of turn dicts with keys: speaker, text, created_at, artifacts_json
    """
    if not supabase_client:
        return []
    
    try:
        result = supabase_client.table("interaction_turns")\
            .select("speaker, text, created_at, artifacts_json")\
            .eq("interaction_id", interaction_id)\
            .order("turn_sequence", desc=False)\
            .limit(limit)\
            .execute()
        
        return result.data or []
    except Exception as e:
        print(f"⚠️ Failed to get conversation turns: {e}")
        return []


def apply_extracted_updates(
    user_id: Optional[str],
    extracted_updates: Dict[str, Any],
    interaction_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Apply extracted structured data updates to database tables.
    Updates are incremental - only non-null fields are updated.
    
    Args:
        user_id: User UUID
        extracted_updates: Dict with keys like "people_profiles", "role_assignments", "organizations"
        interaction_id: Optional interaction ID for logging
        
    Returns:
        Dict with update results: {"people_profiles": True/False, ...}
    """
    if not supabase_client or not user_id:
        return {}
    
    results = {}
    
    try:
        # Update people_profiles
        if "people_profiles" in extracted_updates:
            profile_updates = extracted_updates["people_profiles"]
            # Only update non-null fields
            update_data = {}
            for key in ["first_name", "last_name", "headline", "location"]:
                if key in profile_updates and profile_updates[key] is not None:
                    update_data[key] = profile_updates[key]
            
            if update_data:
                update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
                
                # Use upsert (ON CONFLICT DO UPDATE)
                result = supabase_client.table("people_profiles")\
                    .upsert({
                        "user_id": user_id,
                        **update_data
                    }, on_conflict="user_id")\
                    .execute()
                
                results["people_profiles"] = result.data is not None
        
        # Update role_assignments
        if "role_assignments" in extracted_updates:
            role_data = extracted_updates["role_assignments"]
            if "role" in role_data and role_data["role"]:
                role = role_data["role"]
                confidence = float(role_data.get("confidence", 0.9))
                
                # Upsert role assignment
                result = supabase_client.table("role_assignments")\
                    .upsert({
                        "user_id": user_id,
                        "role": role,
                        "confidence": confidence,
                        "evidence": {
                            "source": "qualification_call",
                            "interaction_id": interaction_id,
                            "extracted_at": datetime.now(timezone.utc).isoformat()
                        }
                    }, on_conflict="user_id,role")\
                    .execute()
                
                results["role_assignments"] = result.data is not None
        
        # Update organizations (for hirers)
        if "organizations" in extracted_updates:
            org_data = extracted_updates["organizations"]
            if "name" in org_data and org_data["name"]:
                org_name = org_data["name"]
                
                # Try to find existing org by name
                existing = supabase_client.table("organizations")\
                    .select("id")\
                    .ilike("name", org_name)\
                    .limit(1)\
                    .execute()
                
                if existing.data:
                    org_id = existing.data[0]["id"]
                else:
                    # Create new org
                    new_org = supabase_client.table("organizations")\
                        .insert({
                            "name": org_name,
                            "created_by_user_id": user_id
                        })\
                        .execute()
                    
                    if new_org.data:
                        org_id = new_org.data[0]["id"]
                    else:
                        org_id = None
                
                results["organizations"] = org_id is not None
        
        return results
        
    except Exception as e:
        print(f"⚠️ Failed to apply extracted updates: {e}")
        import traceback
        traceback.print_exc()
        return results

