"""
Post-call profile extraction service.

After a candidate_chat or employer_brief call completes, uses GPT-4o to
extract structured data from the transcript and updates the database.
"""
import json
import threading
from datetime import datetime, timezone
from typing import Optional

from config.clients import supabase_client, gpt_client


# ---------------------------------------------------------------------------
# Candidate extraction
# ---------------------------------------------------------------------------

_CANDIDATE_EXTRACTION_PROMPT = """You are analysing a recruitment conversation between a consultant and a candidate.

Full call transcript:
{transcript}

Extract structured data from this conversation. Only include fields where the candidate clearly provided information — leave others as null.

Respond ONLY with valid JSON matching this structure:
{{
  "skills": ["skill1", "skill2"],
  "industries": ["industry1"],
  "experience_years": null,
  "current_role": null,
  "desired_role": null,
  "salary_expectation": null,
  "location": null,
  "availability": null,
  "summary": "2-3 sentence summary of who this candidate is and what they want"
}}"""

_EMPLOYER_EXTRACTION_PROMPT = """You are analysing a recruitment conversation between a consultant and a hiring manager.

Full call transcript:
{transcript}

Extract the role brief from this conversation. Only include fields where the employer clearly provided information — leave others as null.

Respond ONLY with valid JSON matching this structure:
{{
  "role_title": null,
  "company": null,
  "industry": null,
  "description": "2-3 sentence description of the role",
  "must_have_skills": ["skill1"],
  "nice_to_have": ["skill1"],
  "salary_range": null,
  "location": null,
  "remote_policy": null,
  "start_date": null,
  "team_size": null,
  "summary": "2-3 sentence summary of what this employer needs"
}}"""


def extract_candidate_profile(interaction_id: str, job_id: str) -> Optional[dict]:
    """Extract candidate data from transcript and update people_profiles."""
    if not supabase_client or not gpt_client:
        print("Supabase or OpenAI client not available for extraction", flush=True)
        return None

    try:
        transcript = _build_transcript(interaction_id)
        if not transcript:
            print(f"Empty transcript for extraction: interaction_id={interaction_id}", flush=True)
            return None

        prompt = _CANDIDATE_EXTRACTION_PROMPT.format(transcript=transcript)
        completion = gpt_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        result = json.loads(completion.choices[0].message.content)
        print(f"Candidate extraction result: {json.dumps(result)[:300]}", flush=True)

        # Store extraction in interaction artifacts
        _store_extraction_in_artifacts(interaction_id, "candidate_extraction", result)

        # Update people_profiles if we have a user_id
        user_id = _get_user_id_from_job(job_id)
        if user_id:
            _update_candidate_profile(user_id, result)

        return result

    except Exception as e:
        print(f"Error extracting candidate profile: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return None


def extract_employer_brief(interaction_id: str, job_id: str) -> Optional[dict]:
    """Extract employer role brief from transcript and create opportunity."""
    if not supabase_client or not gpt_client:
        print("Supabase or OpenAI client not available for extraction", flush=True)
        return None

    try:
        transcript = _build_transcript(interaction_id)
        if not transcript:
            print(f"Empty transcript for extraction: interaction_id={interaction_id}", flush=True)
            return None

        prompt = _EMPLOYER_EXTRACTION_PROMPT.format(transcript=transcript)
        completion = gpt_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        result = json.loads(completion.choices[0].message.content)
        print(f"Employer extraction result: {json.dumps(result)[:300]}", flush=True)

        # Store extraction in interaction artifacts
        _store_extraction_in_artifacts(interaction_id, "employer_extraction", result)

        # Create opportunity record
        user_id = _get_user_id_from_job(job_id)
        if user_id and result.get("role_title"):
            _create_opportunity_from_brief(user_id, result)

        return result

    except Exception as e:
        print(f"Error extracting employer brief: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return None


# ---------------------------------------------------------------------------
# Async wrappers (called from /voice/status webhook)
# ---------------------------------------------------------------------------

def extract_candidate_profile_async(interaction_id: str, job_id: str):
    threading.Thread(
        target=extract_candidate_profile,
        args=(interaction_id, job_id),
        daemon=True,
    ).start()


def extract_employer_brief_async(interaction_id: str, job_id: str):
    threading.Thread(
        target=extract_employer_brief,
        args=(interaction_id, job_id),
        daemon=True,
    ).start()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_transcript(interaction_id: str) -> str:
    try:
        turns_resp = (
            supabase_client.table("interaction_turns")
            .select("speaker, text, turn_sequence")
            .eq("interaction_id", interaction_id)
            .order("turn_sequence", desc=False)
            .execute()
        )
        lines = []
        for t in (turns_resp.data or []):
            speaker = (t.get("speaker") or "").capitalize()
            text = (t.get("text") or "").strip()
            if text:
                lines.append(f"{speaker}: {text}")
        return "\n".join(lines)
    except Exception as e:
        print(f"Could not build transcript for {interaction_id}: {e}", flush=True)
        return ""


def _get_user_id_from_job(job_id: str) -> Optional[str]:
    try:
        resp = (
            supabase_client.table("outbound_call_jobs")
            .select("user_id")
            .eq("id", job_id)
            .limit(1)
            .execute()
        )
        if resp.data:
            return resp.data[0].get("user_id")
    except Exception:
        pass
    return None


def _store_extraction_in_artifacts(interaction_id: str, key: str, data: dict):
    try:
        resp = (
            supabase_client.table("interactions")
            .select("artifacts")
            .eq("id", interaction_id)
            .limit(1)
            .execute()
        )
        artifacts = {}
        if resp.data:
            artifacts = resp.data[0].get("artifacts") or {}
        artifacts[key] = data
        artifacts[f"{key}_at"] = datetime.now(timezone.utc).isoformat()
        supabase_client.table("interactions").update(
            {"artifacts": artifacts}
        ).eq("id", interaction_id).execute()
    except Exception as e:
        print(f"Failed to store extraction in artifacts: {e}", flush=True)


def _update_candidate_profile(user_id: str, extraction: dict):
    """Update or create people_profiles with extracted candidate data."""
    try:
        update = {}
        if extraction.get("industries"):
            update["industries"] = extraction["industries"]
        if extraction.get("skills"):
            update["expertise"] = extraction["skills"]
        if extraction.get("location"):
            update["location"] = extraction["location"]
        if extraction.get("experience_years"):
            update["years_experience"] = extraction["experience_years"]
        if extraction.get("current_role"):
            update["headline"] = extraction["current_role"]
        if extraction.get("summary"):
            update["bio"] = extraction["summary"]
        if extraction.get("salary_expectation"):
            update["rate_range"] = extraction["salary_expectation"]
        if extraction.get("availability"):
            update["availability_type"] = extraction["availability"]

        if not update:
            return

        # Only update fields that are currently empty (don't overwrite existing data)
        existing = (
            supabase_client.table("people_profiles")
            .select("*")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if existing.data:
            profile = existing.data[0] or {}
            filtered = {}
            for key, value in update.items():
                existing_val = profile.get(key)
                if not existing_val or (isinstance(existing_val, list) and len(existing_val) == 0):
                    filtered[key] = value
            if filtered:
                supabase_client.table("people_profiles").update(
                    filtered
                ).eq("user_id", user_id).execute()
                print(f"Updated candidate profile for {user_id}: {list(filtered.keys())}", flush=True)
        else:
            # Create profile
            update["user_id"] = user_id
            update["profile_source"] = "voice_call"
            supabase_client.table("people_profiles").insert(update).execute()
            print(f"Created candidate profile for {user_id}", flush=True)

    except Exception as e:
        print(f"Failed to update candidate profile for {user_id}: {e}", flush=True)


def _create_opportunity_from_brief(user_id: str, extraction: dict):
    """Create an opportunity record from extracted employer brief."""
    try:
        now_iso = datetime.now(timezone.utc).isoformat()

        # Find or create organization
        organization_id = None
        company = extraction.get("company")
        if company:
            org_resp = (
                supabase_client.table("organizations")
                .select("id")
                .eq("name", company)
                .limit(1)
                .execute()
            )
            if org_resp.data:
                organization_id = org_resp.data[0].get("id")
            else:
                org_insert = supabase_client.table("organizations").insert({
                    "name": company,
                    "industry": extraction.get("industry"),
                    "created_by_user_id": user_id,
                }).execute()
                if org_insert.data:
                    organization_id = org_insert.data[0].get("id")

        opp_payload = {
            "created_by_user_id": user_id,
            "organization_id": organization_id,
            "type": "hire_fractional",
            "title": extraction.get("role_title", "Untitled Role"),
            "description": extraction.get("description") or extraction.get("summary", ""),
            "industry": extraction.get("industry"),
            "location": extraction.get("location"),
            "is_remote": bool(extraction.get("remote_policy") and "remote" in str(extraction["remote_policy"]).lower()),
            "compensation": extraction.get("salary_range"),
            "status": "open",
            "metadata": {
                "source": "voice_call",
                "must_have_skills": extraction.get("must_have_skills", []),
                "nice_to_have": extraction.get("nice_to_have", []),
                "remote_policy": extraction.get("remote_policy"),
                "start_date": extraction.get("start_date"),
                "team_size": extraction.get("team_size"),
                "extracted_at": now_iso,
            },
        }
        resp = supabase_client.table("opportunities").insert(opp_payload).execute()
        if resp.data:
            print(f"Created opportunity from voice brief: {resp.data[0].get('id')}", flush=True)

    except Exception as e:
        print(f"Failed to create opportunity from brief: {e}", flush=True)
