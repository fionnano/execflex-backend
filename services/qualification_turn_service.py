"""
Service for managing qualification conversation turns and incremental DB updates.
"""
from typing import Dict, Optional, List, Any
from config.clients import supabase_client
from datetime import datetime, timezone
import uuid
import re


# Keep in sync with Supabase `industry_enum` (see `frontend/supabase/migrations/*convert_industries*`).
_INDUSTRY_ENUM_VALUES: List[str] = [
    "Technology",
    "Financial Services",
    "Healthcare",
    "Manufacturing",
    "Retail",
    "Professional Services",
    "Energy",
    "Education",
    "Government",
    "Non-profit",
    "Media",
    "Telecommunications",
    "Transportation",
    "Real Estate",
    "Agriculture",
    "Other",
]

_INDUSTRY_CANONICAL_BY_KEY: Dict[str, str] = {v.strip().lower(): v for v in _INDUSTRY_ENUM_VALUES}

# Common synonyms/variants from user speech + LLM extraction.
_INDUSTRY_SYNONYMS: Dict[str, str] = {
    # finance
    "finance": "Financial Services",
    "financial": "Financial Services",
    "fintech": "Financial Services",
    "fin tech": "Financial Services",
    # technology
    "tech": "Technology",
    "saas": "Technology",
    "software": "Technology",
    "it": "Technology",
    "ict": "Technology",
    "information technology": "Technology",
    # healthcare
    "health": "Healthcare",
    "medical": "Healthcare",
    "pharma": "Healthcare",
    "pharmaceutical": "Healthcare",
    # manufacturing
    "production": "Manufacturing",
    "industrial": "Manufacturing",
    # retail
    "e-commerce": "Retail",
    "ecommerce": "Retail",
    "consumer goods": "Retail",
    "cpg": "Retail",
    # prof services
    "consulting": "Professional Services",
    "advisory": "Professional Services",
    "services": "Professional Services",
    "business services": "Professional Services",
    "b2b services": "Professional Services",
    "b2b": "Professional Services",
    # energy
    "utilities": "Energy",
    "oil": "Energy",
    "gas": "Energy",
    "renewable energy": "Energy",
    # education
    "edtech": "Education",
    "learning": "Education",
    # government
    "public sector": "Government",
    "public service": "Government",
    # non-profit
    "nonprofit": "Non-profit",
    "ngo": "Non-profit",
    "charity": "Non-profit",
    # media
    "entertainment": "Media",
    "publishing": "Media",
    # telecoms
    "telecom": "Telecommunications",
    "telco": "Telecommunications",
    # transportation
    "logistics": "Transportation",
    "shipping": "Transportation",
    # real estate
    "property": "Real Estate",
    "construction": "Real Estate",
    # agriculture
    "farming": "Agriculture",
    "agri": "Agriculture",
}


def _normalize_industry_value(value: Any) -> Optional[str]:
    """
    Normalize LLM/user-provided industry values to match `industry_enum` labels exactly.
    Returns None if we can't confidently map it (so we skip the DB update rather than error).
    """
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)

    key = re.sub(r"\s+", " ", value.strip().lower())
    if not key:
        return None

    # Direct match against known enum labels (case-insensitive).
    direct = _INDUSTRY_CANONICAL_BY_KEY.get(key)
    if direct:
        return direct

    # Exact synonym match.
    synonym = _INDUSTRY_SYNONYMS.get(key)
    if synonym:
        return synonym

    # Heuristic substring match for common speech patterns like "technology business".
    # Keep this conservative to avoid bad classifications.
    if "technology" in key or "software" in key or "saas" in key:
        return "Technology"
    if "financial" in key or "fintech" in key:
        return "Financial Services"
    if "health" in key or "pharma" in key or "medical" in key:
        return "Healthcare"
    if "manufactur" in key or "industrial" in key:
        return "Manufacturing"
    if "retail" in key or "ecommerce" in key or "e-commerce" in key:
        return "Retail"
    if "consult" in key or "advis" in key or "professional service" in key:
        return "Professional Services"
    if "telecom" in key:
        return "Telecommunications"
    if "logistic" in key or "transport" in key or "shipping" in key:
        return "Transportation"
    if "real estate" in key or "property" in key or "construction" in key:
        return "Real Estate"
    if "agri" in key or "farm" in key:
        return "Agriculture"
    if "government" in key or "public sector" in key:
        return "Government"
    if "nonprofit" in key or "non-profit" in key or "ngo" in key or "charity" in key:
        return "Non-profit"
    if "media" in key or "entertain" in key or "publish" in key:
        return "Media"
    if "energy" in key or "utility" in key or "renewable" in key or "oil" in key or "gas" in key:
        return "Energy"
    if "education" in key or "edtech" in key:
        return "Education"

    return None


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
            for key in ["first_name", "last_name", "headline", "location", "availability_type"]:
                if key in profile_updates and profile_updates[key] is not None:
                    update_data[key] = profile_updates[key]
            
            # Handle industries (array field)
            if "industries" in profile_updates and profile_updates["industries"] is not None:
                industries_value = profile_updates["industries"]
                # Convert to a list of candidate strings
                if isinstance(industries_value, list):
                    candidates = industries_value
                else:
                    candidates = [industries_value]

                normalized: List[str] = []
                for item in candidates:
                    v = _normalize_industry_value(item)
                    if v:
                        normalized.append(v)

                # Deduplicate while preserving order
                deduped: List[str] = []
                seen = set()
                for v in normalized:
                    if v not in seen:
                        deduped.append(v)
                        seen.add(v)

                # Only write if we have at least one valid enum value.
                if deduped:
                    update_data["industries"] = deduped
            
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
                    # Update existing org with industry/location if provided
                    org_update = {}
                    if "industry" in org_data and org_data["industry"]:
                        # Convert single industry string to array
                        if isinstance(org_data["industry"], str):
                            org_update["industry"] = org_data["industry"]
                        else:
                            org_update["industry"] = str(org_data["industry"])
                    if "location" in org_data and org_data["location"]:
                        org_update["location"] = org_data["location"]
                    
                    if org_update:
                        org_update["updated_at"] = datetime.now(timezone.utc).isoformat()
                        supabase_client.table("organizations")\
                            .update(org_update)\
                            .eq("id", org_id)\
                            .execute()
                else:
                    # Create new org
                    new_org_data = {
                        "name": org_name,
                        "created_by_user_id": user_id
                    }
                    if "industry" in org_data and org_data["industry"]:
                        new_org_data["industry"] = org_data["industry"] if isinstance(org_data["industry"], str) else str(org_data["industry"])
                    if "location" in org_data and org_data["location"]:
                        new_org_data["location"] = org_data["location"]
                    
                    new_org = supabase_client.table("organizations")\
                        .insert(new_org_data)\
                        .execute()
                    
                    if new_org.data:
                        org_id = new_org.data[0]["id"]
                    else:
                        org_id = None
                
                results["organizations"] = org_id is not None
        
        # Update role_postings (for hirers - the role they're hiring for)
        if "role_postings" in extracted_updates:
            posting_data = extracted_updates["role_postings"]
            if posting_data and (posting_data.get("title") or posting_data.get("location") or posting_data.get("engagement_type")):
                # Get user's organization_id if available
                org_id = None
                try:
                    # Try to find user's organization
                    org_result = supabase_client.table("organizations")\
                        .select("id")\
                        .eq("created_by_user_id", user_id)\
                        .order("created_at", desc=True)\
                        .limit(1)\
                        .execute()
                    if org_result.data:
                        org_id = org_result.data[0]["id"]
                except Exception:
                    pass
                
                # Create or update role posting
                posting_update = {
                    "user_id": user_id,
                    "status": "draft"  # Default to draft until fully qualified
                }
                if "title" in posting_data and posting_data["title"]:
                    posting_update["title"] = posting_data["title"]
                if "location" in posting_data and posting_data["location"]:
                    posting_update["location"] = posting_data["location"]
                if "engagement_type" in posting_data and posting_data["engagement_type"]:
                    posting_update["engagement_type"] = posting_data["engagement_type"]
                if org_id:
                    posting_update["company_id"] = org_id
                
                # Try to find existing draft posting for this user
                try:
                    existing_posting = supabase_client.table("role_postings")\
                        .select("id")\
                        .eq("user_id", user_id)\
                        .eq("status", "draft")\
                        .order("created_at", desc=True)\
                        .limit(1)\
                        .execute()
                    
                    if existing_posting.data:
                        # Update existing draft
                        posting_update["updated_at"] = datetime.now(timezone.utc).isoformat()
                        supabase_client.table("role_postings")\
                            .update(posting_update)\
                            .eq("id", existing_posting.data[0]["id"])\
                            .execute()
                        results["role_postings"] = True
                    else:
                        # Create new draft posting
                        posting_update["created_at"] = datetime.now(timezone.utc).isoformat()
                        posting_update["updated_at"] = datetime.now(timezone.utc).isoformat()
                        new_posting = supabase_client.table("role_postings")\
                            .insert(posting_update)\
                            .execute()
                        results["role_postings"] = new_posting.data is not None
                except Exception as e:
                    print(f"⚠️ Failed to create/update role posting: {e}")
                    results["role_postings"] = False
        
        return results
        
    except Exception as e:
        print(f"⚠️ Failed to apply extracted updates: {e}")
        import traceback
        traceback.print_exc()
        return results

