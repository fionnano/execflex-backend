"""
Post-call profile extraction service.

After a candidate_chat or employer_brief call completes, uses GPT-4o to
extract structured data from the transcript and updates the database.
"""
import json
import time
import threading
from datetime import datetime, timezone
from typing import Optional

from config.clients import supabase_client, gpt_client


# ---------------------------------------------------------------------------
# Candidate extraction
# ---------------------------------------------------------------------------

_CANDIDATE_EXTRACTION_PROMPT = """You are extracting structured candidate profile data from a recruitment conversation transcript. Extract EVERY piece of information mentioned, even if it was said casually or in passing. Be thorough — if they mentioned a city, that's their location. If they mentioned a number or range, that's their salary expectation. If they mentioned an industry or sector, add it to industries.

Full call transcript:
{transcript}

Extract this JSON:
{{
  "skills": ["list every skill, technology, or competency mentioned"],
  "industries": ["every industry or sector mentioned — e.g. technology, healthcare, finance"],
  "experience_years": null,
  "current_role": "their current or most recent job title",
  "desired_role": "what they said they're looking for — NEVER use 'General Screening' or 'Not provided'",
  "salary_expectation": "any salary, rate, or compensation mentioned — include the range if given",
  "location": "any city, country, or region mentioned as where they are or want to work",
  "availability": "when they can start — immediately, notice period, specific date",
  "summary": "2-3 sentence summary of who this person is and what they want"
}}

Rules:
- If they didn't mention something, set it to null — NEVER use 'Not provided', 'General Screening', 'N/A', or any placeholder text
- Extract from the FULL transcript, not just direct answers to questions
- If they said 'I'm based in Cork' at any point, location is 'Cork, Ireland'
- If they said 'around 80k' at any point, salary_expectation is '€80,000'
- If they mentioned working in 'tech' or 'fintech', add those to industries
- Be generous in extraction — capture everything possible
- For desired_role, use what the candidate ACTUALLY said they want, not the type of call

Return ONLY valid JSON, no markdown."""

_EMPLOYER_EXTRACTION_PROMPT = """You are analysing a recruitment conversation between a consultant (Dan) and a hiring manager.

Full call transcript:
{transcript}

Extract ALL details about the role brief from this conversation. Be thorough — pull out every requirement, preference, and detail mentioned.

For fields where the employer did NOT provide information at all, use null.
For list fields where they mentioned nothing, use an empty array [].

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

# Max seconds to wait for transcript turns to finish writing
_TRANSCRIPT_WAIT_TIMEOUT = 15
_TRANSCRIPT_POLL_INTERVAL = 2


def _wait_for_transcript(interaction_id: str) -> str:
    """
    Wait for transcript turns to be fully written, then build transcript.

    The WebSocket handler writes turns in a background thread that may still
    be flushing when the Twilio status callback arrives. Poll until we see
    turns or timeout.
    """
    deadline = time.time() + _TRANSCRIPT_WAIT_TIMEOUT
    best_transcript = ""
    prev_turn_count = 0

    while time.time() < deadline:
        try:
            turns_resp = (
                supabase_client.table("interaction_turns")
                .select("speaker, text, turn_sequence")
                .eq("interaction_id", interaction_id)
                .order("turn_sequence", desc=False)
                .execute()
            )
            turns = turns_resp.data or []
            turn_count = len(turns)

            if turn_count > 0:
                lines = []
                for t in turns:
                    speaker = (t.get("speaker") or "").capitalize()
                    text = (t.get("text") or "").strip()
                    if text:
                        lines.append(f"{speaker}: {text}")
                best_transcript = "\n".join(lines)

                # If turn count hasn't changed since last poll, transcript is likely complete
                if turn_count == prev_turn_count and turn_count >= 2:
                    print(
                        f"[Extraction] Transcript stable at {turn_count} turns for {interaction_id}",
                        flush=True,
                    )
                    break
                prev_turn_count = turn_count

        except Exception as e:
            print(f"[Extraction] Error polling turns for {interaction_id}: {e}", flush=True)

        time.sleep(_TRANSCRIPT_POLL_INTERVAL)

    if not best_transcript:
        # Last resort: try interaction.transcript_text (finalized by status callback)
        try:
            ix_resp = (
                supabase_client.table("interactions")
                .select("transcript_text")
                .eq("id", interaction_id)
                .limit(1)
                .execute()
            )
            if ix_resp.data:
                best_transcript = (ix_resp.data[0] or {}).get("transcript_text") or ""
                if best_transcript:
                    print(
                        f"[Extraction] Using fallback transcript_text for {interaction_id} "
                        f"({len(best_transcript)} chars)",
                        flush=True,
                    )
        except Exception:
            pass

    return best_transcript


_PLACEHOLDER_VALUES = {
    "general screening", "not provided", "n/a", "none", "unknown",
    "not mentioned", "not specified", "not discussed", "not applicable",
}


def _clean_extraction_result(result: dict) -> dict:
    """Remove placeholder values that GPT sometimes generates instead of null."""
    for key, value in result.items():
        if isinstance(value, str) and value.strip().lower() in _PLACEHOLDER_VALUES:
            result[key] = None
    return result


def _second_pass_extraction(transcript: str, first_result: dict, missing_fields: list) -> dict:
    """Re-read transcript to fill fields the first pass missed."""
    if not gpt_client or not missing_fields:
        return first_result

    fields_desc = ", ".join(missing_fields)
    print(f"[Extraction] Pass 2: attempting to fill missing fields: {missing_fields}", flush=True)

    # Build targeted hints for each missing field
    field_hints = {
        "salary_expectation": "any mention of salary, compensation, rate, package, money, pay, earnings, or numbers with k/K/thousand",
        "experience_years": "any mention of years, time in role, career length, started in [year], or phrases like 'for the last X years'",
        "location": "any city, town, county, country, or area mentioned",
        "industries": "any sector, field, or industry the person works in or mentioned",
        "availability": "any mention of when they can start, notice period, or timeline",
        "desired_role": "what they said they WANT to do next — a job title or type of work",
        "current_role": "their CURRENT or most recent job title",
        "skills": "any skill, technology, tool, or competency mentioned",
    }
    hints_block = "\n".join(
        f"- {f}: {field_hints.get(f, 'any mention of ' + f)}"
        for f in missing_fields
    )

    second_prompt = f"""The following fields were not extracted from the conversation. Re-read the transcript carefully — the person may have mentioned these casually or in passing:

{hints_block}

Full transcript:
{transcript}

Extraction hints:
- "salary_expectation": ANY mention of money, pay, rate, package, compensation, or numbers with k/K
- "location": ANY city, town, county, country, or area mentioned
- "industries": ANY sector, field, or industry the person works in or mentioned
- "availability": ANY mention of when they can start, notice period, or timeline
- "desired_role": What they said they WANT to do next — a job title or type of work
- "current_role": Their CURRENT or most recent job title
- "skills": ANY skill, technology, tool, or competency mentioned

Return ONLY a JSON object with the fields you found. Only include fields where you found actual information. Do NOT include fields that genuinely weren't mentioned. Do NOT use placeholder values like 'Not provided'.

Example: {{"location": "Dublin, Ireland", "salary_expectation": "€90,000-€100,000"}}"""

    try:
        completion = gpt_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": second_prompt}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        raw = completion.choices[0].message.content
        print(f"[Extraction] Pass 2 response: {raw[:400]}", flush=True)
        second_result = json.loads(raw)

        filled = []
        for key, value in second_result.items():
            if value is not None and value != [] and value != "":
                if isinstance(value, str) and value.strip().lower() in _PLACEHOLDER_VALUES:
                    continue
                if first_result.get(key) is None or first_result.get(key) == []:
                    first_result[key] = value
                    filled.append(key)

        if filled:
            print(f"[Extraction] Pass 2 filled {len(filled)} fields: {filled}", flush=True)
        else:
            print("[Extraction] Pass 2 found no additional data", flush=True)

    except Exception as e:
        print(f"[Extraction] Pass 2 failed: {e}", flush=True)

    return first_result


def extract_candidate_profile(interaction_id: str, job_id: str) -> Optional[dict]:
    """Extract candidate data from transcript and update people_profiles."""
    print(f"[Extraction] Starting candidate extraction: interaction={interaction_id}, job={job_id}", flush=True)

    if not supabase_client or not gpt_client:
        print("[Extraction] FAILED: Supabase or OpenAI client not available", flush=True)
        return None

    try:
        transcript = _wait_for_transcript(interaction_id)
        if not transcript:
            print(f"[Extraction] FAILED: Empty transcript after waiting: interaction={interaction_id}", flush=True)
            _store_extraction_in_artifacts(interaction_id, "candidate_extraction", {
                "error": "empty_transcript",
                "message": "No transcript turns found after waiting",
            })
            return None

        print(
            f"[Extraction] Transcript ready ({len(transcript)} chars, "
            f"{transcript.count(chr(10)) + 1} lines). Sending to GPT-4o...",
            flush=True,
        )
        print(f"[Extraction] Transcript preview: {transcript[:500]}...", flush=True)

        prompt = _CANDIDATE_EXTRACTION_PROMPT.format(transcript=transcript)
        completion = gpt_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        raw_response = completion.choices[0].message.content
        print(f"[Extraction] GPT-4o raw response: {raw_response[:600]}", flush=True)

        result = json.loads(raw_response)

        # Count non-null extracted fields
        extracted_fields = [k for k, v in result.items() if v is not None and v != [] and v != ""]
        print(
            f"[Extraction] Pass 1: extracted {len(extracted_fields)} non-empty fields: {extracted_fields}",
            flush=True,
        )

        # Second pass: try to fill missing key fields
        key_fields = ["skills", "industries", "salary_expectation", "location", "availability", "desired_role", "current_role"]
        missing_fields = [f for f in key_fields if result.get(f) is None or result.get(f) == []]
        if missing_fields and transcript:
            result = _second_pass_extraction(transcript, result, missing_fields)

        # Clean up placeholder values GPT might still produce
        result = _clean_extraction_result(result)

        # Store extraction in interaction artifacts
        _store_extraction_in_artifacts(interaction_id, "candidate_extraction", result)
        print(f"[Extraction] Stored extraction in artifacts for interaction {interaction_id}", flush=True)

        # Update people_profiles if we have a user_id
        user_id = _get_user_id_from_job(job_id)
        if user_id:
            _update_candidate_profile(user_id, result)
        else:
            print(f"[Extraction] No user_id found for job {job_id}, skipping profile update", flush=True)

        return result

    except Exception as e:
        print(f"[Extraction] ERROR extracting candidate profile: {e}", flush=True)
        import traceback
        traceback.print_exc()
        _store_extraction_in_artifacts(interaction_id, "candidate_extraction", {
            "error": str(type(e).__name__),
            "message": str(e)[:500],
        })
        return None


def extract_employer_brief(interaction_id: str, job_id: str) -> Optional[dict]:
    """Extract employer role brief from transcript and create opportunity."""
    print(f"[Extraction] Starting employer extraction: interaction={interaction_id}, job={job_id}", flush=True)

    if not supabase_client or not gpt_client:
        print("[Extraction] FAILED: Supabase or OpenAI client not available", flush=True)
        return None

    try:
        transcript = _wait_for_transcript(interaction_id)
        if not transcript:
            print(f"[Extraction] FAILED: Empty transcript after waiting: interaction={interaction_id}", flush=True)
            _store_extraction_in_artifacts(interaction_id, "employer_extraction", {
                "error": "empty_transcript",
                "message": "No transcript turns found after waiting",
            })
            return None

        print(
            f"[Extraction] Transcript ready ({len(transcript)} chars, "
            f"{transcript.count(chr(10)) + 1} lines). Sending to GPT-4o...",
            flush=True,
        )
        print(f"[Extraction] Transcript preview: {transcript[:500]}...", flush=True)

        prompt = _EMPLOYER_EXTRACTION_PROMPT.format(transcript=transcript)
        completion = gpt_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        raw_response = completion.choices[0].message.content
        print(f"[Extraction] GPT-4o raw response: {raw_response[:600]}", flush=True)

        result = json.loads(raw_response)

        extracted_fields = [k for k, v in result.items() if v is not None and v != [] and v != ""]
        print(
            f"[Extraction] Extracted {len(extracted_fields)} non-empty fields: {extracted_fields}",
            flush=True,
        )

        # Store extraction in interaction artifacts
        _store_extraction_in_artifacts(interaction_id, "employer_extraction", result)
        print(f"[Extraction] Stored extraction in artifacts for interaction {interaction_id}", flush=True)

        # Create opportunity record
        user_id = _get_user_id_from_job(job_id)
        if user_id and result.get("role_title"):
            _create_opportunity_from_brief(user_id, result)
        else:
            print(
                f"[Extraction] Skipping opportunity creation: user_id={user_id}, "
                f"role_title={result.get('role_title')}",
                flush=True,
            )

        return result

    except Exception as e:
        print(f"[Extraction] ERROR extracting employer brief: {e}", flush=True)
        import traceback
        traceback.print_exc()
        _store_extraction_in_artifacts(interaction_id, "employer_extraction", {
            "error": str(type(e).__name__),
            "message": str(e)[:500],
        })
        return None


# ---------------------------------------------------------------------------
# Async wrappers (called from /voice/status webhook)
# ---------------------------------------------------------------------------

def extract_candidate_profile_async(interaction_id: str, job_id: str):
    print(f"[Extraction] Launching async candidate extraction thread: interaction={interaction_id}", flush=True)
    threading.Thread(
        target=extract_candidate_profile,
        args=(interaction_id, job_id),
        daemon=True,
    ).start()


def extract_employer_brief_async(interaction_id: str, job_id: str):
    print(f"[Extraction] Launching async employer extraction thread: interaction={interaction_id}", flush=True)
    threading.Thread(
        target=extract_employer_brief,
        args=(interaction_id, job_id),
        daemon=True,
    ).start()


# ---------------------------------------------------------------------------
# Talent network extraction — proactive career-intention call
# ---------------------------------------------------------------------------

_TALENT_NETWORK_EXTRACTION_PROMPT = """You are extracting structured career intention data from a proactive "talent network" outreach call. The call is a brief 4-minute career chat — NOT a role-specific screening. Aidan asked five questions:
1. Are you currently open to new opportunities?
2. What type of role interests you most — full-time, fractional, board/NED, or mixed?
3. What sectors or industries are you passionate about?
4. What salary or day rate are you targeting?
5. What's your notice period or availability?

Extract the candidate's answers into this exact JSON schema. If the candidate didn't answer a question clearly, set the field to null. Never invent data.

{{
  "open_to_opportunities": "yes" | "no" | "passive" | null,
  "preferred_role_type": "full_time" | "fractional" | "ned" | "mixed" | null,
  "preferred_sectors": ["list of sectors/industries mentioned, lowercase"],
  "salary_expectation": "verbatim string with range and currency if stated, else null",
  "availability": "verbatim string describing notice period or start date, else null",
  "notes": "2-3 sentence summary of anything else relevant the candidate said about their career"
}}

Rules:
- "yes" means actively looking; "passive" means not looking but would listen; "no" means definitely not interested.
- preferred_role_type must be exactly one of the four enum values (or null). Map casual phrasing: "consulting" -> fractional, "board work" -> ned, "a mix" -> mixed.
- preferred_sectors is a list of short tags (e.g. ["saas", "fintech", "healthcare"]). Empty list if none mentioned.
- Currency: if they said "150k" assume EUR; if they said "£" use GBP; preserve what they actually said.
- Return ONLY valid JSON. No markdown, no prose.

Full call transcript:
{transcript}
"""


def extract_talent_network(interaction_id: str, job_id: str) -> Optional[dict]:
    """
    Extract career-intention data from a talent_network call transcript
    and persist it to both interactions.artifacts.talent_network_data
    and the candidate's people_profiles row.
    """
    print(
        f"[TalentNet] Starting talent_network extraction: "
        f"interaction={interaction_id}, job={job_id}",
        flush=True,
    )
    if not supabase_client or not gpt_client:
        print("[TalentNet] FAILED: Supabase or OpenAI client not available", flush=True)
        return None

    try:
        transcript = _wait_for_transcript(interaction_id)
        if not transcript:
            print(f"[TalentNet] FAILED: Empty transcript: interaction={interaction_id}", flush=True)
            _store_extraction_in_artifacts(interaction_id, "talent_network_data", {
                "error": "empty_transcript",
                "message": "No transcript turns found after waiting",
            })
            return None

        print(
            f"[TalentNet] Transcript ready ({len(transcript)} chars). Calling GPT-4o...",
            flush=True,
        )
        prompt = _TALENT_NETWORK_EXTRACTION_PROMPT.format(transcript=transcript)
        completion = gpt_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        raw = completion.choices[0].message.content
        print(f"[TalentNet] GPT-4o raw response: {raw[:400]}", flush=True)
        result = json.loads(raw)

        # Normalise + guard the enum fields
        valid_open = {"yes", "no", "passive"}
        if result.get("open_to_opportunities") not in valid_open:
            result["open_to_opportunities"] = None
        valid_role_types = {"full_time", "fractional", "ned", "mixed"}
        if result.get("preferred_role_type") not in valid_role_types:
            result["preferred_role_type"] = None
        if not isinstance(result.get("preferred_sectors"), list):
            result["preferred_sectors"] = []
        result["extracted_at"] = datetime.now(timezone.utc).isoformat()

        _store_extraction_in_artifacts(interaction_id, "talent_network_data", result)
        print(
            f"[TalentNet] Stored: open={result.get('open_to_opportunities')!r} "
            f"role_type={result.get('preferred_role_type')!r} "
            f"sectors={result.get('preferred_sectors')}",
            flush=True,
        )

        # Update the candidate's people_profiles row with the preferences.
        # The candidate might be a PDL/CSV-sourced row (no user_id) — in that
        # case source_candidate_id on the job points to the people_profiles id
        # directly. Signup-path candidates have a user_id on the job that
        # matches people_profiles.user_id.
        try:
            job_resp = (
                supabase_client.table("outbound_call_jobs")
                .select("user_id, artifacts")
                .eq("id", job_id)
                .limit(1)
                .execute()
            )
            if job_resp.data:
                job = job_resp.data[0] or {}
                job_user_id = job.get("user_id")
                artifacts = job.get("artifacts") or {}
                ctx = artifacts.get("screening_context") or {}
                source_candidate_id = ctx.get("source_candidate_id")

                # Build the profile update payload. Merge talent_network_data
                # into source_metadata so the same field is queryable from
                # both the interaction and the profile.
                profile_sm_update = {
                    "talent_network_data": result,
                    "talent_network_captured_at": result["extracted_at"],
                }

                if job_user_id:
                    _merge_source_metadata_by_user_id(job_user_id, profile_sm_update)
                elif source_candidate_id:
                    _merge_source_metadata_by_profile_id(source_candidate_id, profile_sm_update)
                else:
                    print("[TalentNet] No user_id or source_candidate_id on job — skipping profile update", flush=True)
        except Exception as e:
            print(f"[TalentNet] Profile update failed: {e}", flush=True)

        return result
    except Exception as e:
        print(f"[TalentNet] ERROR: {e}", flush=True)
        import traceback
        traceback.print_exc()
        _store_extraction_in_artifacts(interaction_id, "talent_network_data", {
            "error": str(type(e).__name__),
            "message": str(e)[:500],
        })
        return None


def extract_talent_network_async(interaction_id: str, job_id: str):
    print(f"[TalentNet] Launching async extraction thread: interaction={interaction_id}", flush=True)
    threading.Thread(
        target=extract_talent_network,
        args=(interaction_id, job_id),
        daemon=True,
    ).start()


def _merge_source_metadata_by_user_id(user_id: str, updates: dict) -> None:
    """Merge a dict into people_profiles.source_metadata for a signup-path row."""
    try:
        resp = (
            supabase_client.table("people_profiles")
            .select("id, source_metadata")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if not resp.data:
            print(f"[TalentNet] No people_profiles row for user_id={user_id}", flush=True)
            return
        row = resp.data[0]
        merged = dict(row.get("source_metadata") or {})
        merged.update(updates)
        supabase_client.table("people_profiles").update({"source_metadata": merged}).eq("id", row["id"]).execute()
        print(f"[TalentNet] Merged talent_network_data into people_profiles id={row['id']}", flush=True)
    except Exception as e:
        print(f"[TalentNet] _merge_source_metadata_by_user_id failed: {e}", flush=True)


def _merge_source_metadata_by_profile_id(profile_id: str, updates: dict) -> None:
    """Merge a dict into people_profiles.source_metadata for a sourced/uploaded row."""
    try:
        resp = (
            supabase_client.table("people_profiles")
            .select("id, source_metadata")
            .eq("id", profile_id)
            .limit(1)
            .execute()
        )
        if not resp.data:
            print(f"[TalentNet] No people_profiles row for id={profile_id}", flush=True)
            return
        row = resp.data[0]
        merged = dict(row.get("source_metadata") or {})
        merged.update(updates)
        supabase_client.table("people_profiles").update({"source_metadata": merged}).eq("id", row["id"]).execute()
        print(f"[TalentNet] Merged talent_network_data into people_profiles id={profile_id}", flush=True)
    except Exception as e:
        print(f"[TalentNet] _merge_source_metadata_by_profile_id failed: {e}", flush=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
            uid = resp.data[0].get("user_id")
            print(f"[Extraction] Resolved user_id={uid} from job {job_id}", flush=True)
            return uid
    except Exception as e:
        print(f"[Extraction] Failed to resolve user_id from job {job_id}: {e}", flush=True)
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
        print(f"[Extraction] Saved {key} to interaction {interaction_id} artifacts", flush=True)
    except Exception as e:
        print(f"[Extraction] FAILED to store {key} in artifacts for {interaction_id}: {e}", flush=True)


def _update_candidate_profile(user_id: str, extraction: dict):
    """Update people_profiles with extracted candidate data."""
    try:
        # Build bio from summary + skills (skills go in bio, NOT expertise which is an enum)
        bio_parts = []
        if extraction.get("summary"):
            bio_parts.append(extraction["summary"])
        if extraction.get("skills"):
            skills_str = ", ".join(extraction["skills"]) if isinstance(extraction["skills"], list) else str(extraction["skills"])
            bio_parts.append(f"Skills: {skills_str}")
        if extraction.get("desired_role"):
            bio_parts.append(f"Looking for: {extraction['desired_role']}")

        update = {}
        if extraction.get("industries"):
            update["industries"] = extraction["industries"]
        # Do NOT write skills to expertise — it's an enum column
        if extraction.get("location"):
            update["location"] = extraction["location"]
        if extraction.get("experience_years"):
            update["years_experience"] = extraction["experience_years"]
        if extraction.get("current_role"):
            update["headline"] = extraction["current_role"]
        if bio_parts:
            update["bio"] = " | ".join(bio_parts)
        if extraction.get("salary_expectation"):
            update["rate_range"] = extraction["salary_expectation"]
        if extraction.get("availability"):
            update["availability_type"] = extraction["availability"]

        if not update:
            print(f"[Extraction] No fields to update for user {user_id} (all null)", flush=True)
            return

        print(f"[Extraction] Candidate profile update payload ({len(update)} fields): {list(update.keys())}", flush=True)

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
                is_empty = (
                    not existing_val
                    or (isinstance(existing_val, list) and len(existing_val) == 0)
                    or existing_val == "Not provided"
                )
                is_our_data = profile.get("profile_source") == "voice_call"
                if is_empty or is_our_data:
                    filtered[key] = value

            if filtered:
                filtered["profile_source"] = "voice_call"
                # Write each field individually to avoid one bad field blocking all updates
                for key, value in filtered.items():
                    try:
                        supabase_client.table("people_profiles").update(
                            {key: value}
                        ).eq("user_id", user_id).execute()
                    except Exception as field_err:
                        print(f"[Extraction] Field {key} failed for {user_id}: {field_err}", flush=True)
                print(f"[Extraction] Updated profile for {user_id}: {list(filtered.keys())}", flush=True)
            else:
                print(f"[Extraction] All fields already populated for {user_id}, skipping update", flush=True)
        else:
            # Create profile — write safe fields only
            update["user_id"] = user_id
            update["profile_source"] = "voice_call"
            try:
                supabase_client.table("people_profiles").insert(update).execute()
                print(f"[Extraction] Created new profile for {user_id}", flush=True)
            except Exception as insert_err:
                print(f"[Extraction] Profile insert failed, trying without problematic fields: {insert_err}", flush=True)
                # Retry with only safe text fields
                safe = {k: v for k, v in update.items() if k in ("user_id", "profile_source", "headline", "bio", "location", "rate_range")}
                if safe.get("user_id"):
                    supabase_client.table("people_profiles").insert(safe).execute()
                    print(f"[Extraction] Created profile with safe fields: {list(safe.keys())}", flush=True)

    except Exception as e:
        print(f"[Extraction] FAILED to update profile for {user_id}: {e}", flush=True)
        import traceback
        traceback.print_exc()


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
            print(f"[Extraction] Created opportunity: {resp.data[0].get('id')}", flush=True)

    except Exception as e:
        print(f"[Extraction] FAILED to create opportunity: {e}", flush=True)
        import traceback
        traceback.print_exc()
