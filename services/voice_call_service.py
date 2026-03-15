"""
Voice call service — job creation and post-call AI analysis for:
  - Onboarding welcome calls
  - Reference check calls
  - Exit interview calls

Each uses the same Twilio outbound pipeline as screening, but with
different call_type values, prompts, and post-call processing.
"""
import json
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests as http_requests

from config.clients import supabase_client, gpt_client


# ---------------------------------------------------------------------------
# Job creation helpers
# ---------------------------------------------------------------------------

def _create_call_job(
    phone: str,
    call_type: str,
    call_context: Dict[str, Any],
    callback_url: Optional[str],
) -> Dict[str, Any]:
    """
    Insert an outbound_call_job + interaction row.
    The existing Twilio dispatcher picks up queued jobs automatically.
    """
    if not supabase_client:
        raise RuntimeError("Supabase client not available")

    now_iso = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()

    # Normalise phone to E.164
    phone = phone.strip()
    if not phone.startswith("+"):
        phone = "+" + phone.replace(" ", "").replace("-", "")

    interaction_resp = supabase_client.table("interactions").insert({
        "channel": "voice",
        "direction": "outbound",
        "provider": "twilio",
        "started_at": now_iso,
        "created_at": now_iso,
    }).execute()
    interaction_id = interaction_resp.data[0]["id"] if interaction_resp.data else None

    job_resp = supabase_client.table("outbound_call_jobs").insert({
        "phone_e164": phone,
        "status": "queued",
        "thread_id": None,
        "interaction_id": interaction_id,
        "created_at": now_iso,
        "updated_at": now_iso,
        "artifacts": {
            "call_type": call_type,
            "created_at": now_iso,
            "screening_context": {   # reuse key — voice_websocket reads this
                **call_context,
                "callback_url": callback_url,
            },
        },
    }).execute()
    job_id = job_resp.data[0]["id"] if job_resp.data else None

    return {
        "job_id": job_id,
        "interaction_id": interaction_id,
        "status": "queued",
    }


# ---------------------------------------------------------------------------
# Onboarding welcome call
# ---------------------------------------------------------------------------

def create_onboarding_call_job(
    employee_phone: str,
    employee_name: str,
    company_name: str,
    start_date: str,
    manager_name: str,
    office_location: str,
    first_day_instructions: str,
    callback_url: Optional[str],
    source_tracker_id: Optional[str],
) -> Dict[str, Any]:
    return _create_call_job(
        phone=employee_phone,
        call_type="onboarding_welcome",
        call_context={
            "employee_name": employee_name,
            "company_name": company_name,
            "start_date": start_date,
            "manager_name": manager_name,
            "office_location": office_location,
            "first_day_instructions": first_day_instructions,
            "source_tracker_id": source_tracker_id,
        },
        callback_url=callback_url,
    )


def process_onboarding_call(interaction_id: str, job_id: str):
    """Post-call: build transcript, fire callback with transcript."""
    if not supabase_client:
        return

    try:
        job_resp = (
            supabase_client.table("outbound_call_jobs")
            .select("*").eq("id", job_id).limit(1).execute()
        )
        if not job_resp.data:
            return
        job = job_resp.data[0]
        artifacts = job.get("artifacts", {}) or {}
        ctx = artifacts.get("screening_context", {}) or {}
        callback_url = ctx.get("callback_url")

        transcript = _build_transcript(interaction_id)
        if not transcript:
            print(f"⚠️ Empty transcript for onboarding call {interaction_id}")

        call_status = artifacts.get("call_status", "completed")

        if callback_url:
            _fire_callback(callback_url, {
                "source_tracker_id": ctx.get("source_tracker_id"),
                "transcript": transcript,
                "call_status": call_status,
                "call_duration_seconds": int(artifacts.get("call_duration") or 0),
            }, job_id)

    except Exception as e:
        print(f"❌ Error processing onboarding call: {e}")
        import traceback; traceback.print_exc()


def process_onboarding_call_async(interaction_id: str, job_id: str):
    threading.Thread(target=process_onboarding_call, args=(interaction_id, job_id), daemon=True).start()


# ---------------------------------------------------------------------------
# Reference check call
# ---------------------------------------------------------------------------

def create_reference_call_job(
    referee_phone: str,
    referee_name: str,
    candidate_name: str,
    role_title: str,
    company_name: str,
    relationship: str,
    questions: List[str],
    callback_url: Optional[str],
    source_ref_id: Optional[str],
) -> Dict[str, Any]:
    return _create_call_job(
        phone=referee_phone,
        call_type="reference_check",
        call_context={
            "referee_name": referee_name,
            "candidate_name": candidate_name,
            "role_title": role_title,
            "company_name": company_name,
            "relationship": relationship,
            "questions": questions,
            "source_ref_id": source_ref_id,
        },
        callback_url=callback_url,
    )


def process_reference_call(interaction_id: str, job_id: str):
    """Post-call: AI summary + sentiment, fire callback."""
    if not supabase_client or not gpt_client:
        return

    try:
        job_resp = (
            supabase_client.table("outbound_call_jobs")
            .select("*").eq("id", job_id).limit(1).execute()
        )
        if not job_resp.data:
            return
        job = job_resp.data[0]
        artifacts = job.get("artifacts", {}) or {}
        ctx = artifacts.get("screening_context", {}) or {}
        callback_url = ctx.get("callback_url")

        transcript = _build_transcript(interaction_id)
        if not transcript:
            print(f"⚠️ Empty transcript for reference call {interaction_id}")
            return

        candidate_name = ctx.get("candidate_name", "the candidate")
        role_title = ctx.get("role_title", "the role")
        company_name = ctx.get("company_name", "the company")

        scoring_prompt = f"""You are analysing a reference check call made on behalf of {company_name}.
The call was conducted with a referee for {candidate_name} who applied for {role_title}.

Full call transcript:
{transcript}

Provide a structured analysis as JSON:
{{
  "summary": "2-3 sentence overall summary of the referee's feedback",
  "sentiment": "positive" | "mixed" | "negative",
  "sentiment_reason": "one sentence explaining the sentiment",
  "strengths": ["strength 1", "strength 2"],
  "concerns": ["concern 1"] or [],
  "would_rehire": true | false | null,
  "key_quotes": ["notable direct quote from referee"]
}}

Respond ONLY with valid JSON."""

        completion = gpt_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": scoring_prompt}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        result = json.loads(completion.choices[0].message.content)
        call_status = artifacts.get("call_status", "completed")

        if callback_url:
            _fire_callback(callback_url, {
                "source_ref_id": ctx.get("source_ref_id"),
                "transcript": transcript,
                "summary": result.get("summary", ""),
                "sentiment": result.get("sentiment", "mixed"),
                "sentiment_reason": result.get("sentiment_reason", ""),
                "strengths": result.get("strengths", []),
                "concerns": result.get("concerns", []),
                "would_rehire": result.get("would_rehire"),
                "key_quotes": result.get("key_quotes", []),
                "call_status": call_status,
                "call_duration_seconds": int(artifacts.get("call_duration") or 0),
            }, job_id)

    except Exception as e:
        print(f"❌ Error processing reference call: {e}")
        import traceback; traceback.print_exc()


def process_reference_call_async(interaction_id: str, job_id: str):
    threading.Thread(target=process_reference_call, args=(interaction_id, job_id), daemon=True).start()


# ---------------------------------------------------------------------------
# Exit interview call
# ---------------------------------------------------------------------------

def create_exit_interview_call_job(
    employee_phone: str,
    employee_name: str,
    company_name: str,
    role_title: str,
    tenure: str,
    manager_name: str,
    callback_url: Optional[str],
    source_user_id: Optional[str],
) -> Dict[str, Any]:
    return _create_call_job(
        phone=employee_phone,
        call_type="exit_interview",
        call_context={
            "employee_name": employee_name,
            "company_name": company_name,
            "role_title": role_title,
            "tenure": tenure,
            "manager_name": manager_name,
            "source_user_id": source_user_id,
        },
        callback_url=callback_url,
    )


def process_exit_interview_call(interaction_id: str, job_id: str):
    """Post-call: AI sentiment scoring per answer + themes + summary, fire callback."""
    if not supabase_client or not gpt_client:
        return

    try:
        job_resp = (
            supabase_client.table("outbound_call_jobs")
            .select("*").eq("id", job_id).limit(1).execute()
        )
        if not job_resp.data:
            return
        job = job_resp.data[0]
        artifacts = job.get("artifacts", {}) or {}
        ctx = artifacts.get("screening_context", {}) or {}
        callback_url = ctx.get("callback_url")

        transcript = _build_transcript(interaction_id)
        if not transcript:
            print(f"⚠️ Empty transcript for exit interview {interaction_id}")
            return

        employee_name = ctx.get("employee_name", "the employee")
        company_name = ctx.get("company_name", "the company")
        role_title = ctx.get("role_title", "their role")
        tenure = ctx.get("tenure", "their time")

        scoring_prompt = f"""You are analysing a confidential AI exit interview for {employee_name},
who worked as {role_title} at {company_name} for {tenure}.

Full call transcript:
{transcript}

Provide a structured analysis as JSON:
{{
  "summary": "3-4 sentence executive summary of the key feedback themes",
  "overall_sentiment": "positive" | "mixed" | "negative",
  "key_themes": ["theme 1", "theme 2", "theme 3"],
  "sentiment_scores": {{
    "reason_for_leaving": "positive" | "mixed" | "negative" | "not_mentioned",
    "enjoyment": "positive" | "mixed" | "negative" | "not_mentioned",
    "company_improvement": "positive" | "mixed" | "negative" | "not_mentioned",
    "manager_relationship": "positive" | "mixed" | "negative" | "not_mentioned",
    "would_recommend": "positive" | "mixed" | "negative" | "not_mentioned"
  }},
  "retention_risk_signals": ["signal 1"] or [],
  "actionable_feedback": ["action 1", "action 2"]
}}

Respond ONLY with valid JSON."""

        completion = gpt_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": scoring_prompt}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        result = json.loads(completion.choices[0].message.content)
        call_status = artifacts.get("call_status", "completed")

        if callback_url:
            _fire_callback(callback_url, {
                "source_user_id": ctx.get("source_user_id"),
                "transcript": transcript,
                "summary": result.get("summary", ""),
                "overall_sentiment": result.get("overall_sentiment", "mixed"),
                "key_themes": result.get("key_themes", []),
                "sentiment_scores": result.get("sentiment_scores", {}),
                "retention_risk_signals": result.get("retention_risk_signals", []),
                "actionable_feedback": result.get("actionable_feedback", []),
                "call_status": call_status,
                "call_duration_seconds": int(artifacts.get("call_duration") or 0),
            }, job_id)

    except Exception as e:
        print(f"❌ Error processing exit interview: {e}")
        import traceback; traceback.print_exc()


def process_exit_interview_call_async(interaction_id: str, job_id: str):
    threading.Thread(target=process_exit_interview_call, args=(interaction_id, job_id), daemon=True).start()


# ---------------------------------------------------------------------------
# Shared helpers
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
        print(f"⚠️ Could not build transcript for {interaction_id}: {e}")
        return ""


def _fire_callback(callback_url: str, payload: Dict[str, Any], job_id: Optional[str] = None):
    try:
        resp = http_requests.post(callback_url, json=payload, timeout=15)
        if resp.status_code >= 400:
            print(f"⚠️ Callback {callback_url} returned {resp.status_code}: {resp.text[:200]}")
        else:
            print(f"✅ Callback delivered to {callback_url} (status {resp.status_code})")
    except Exception as e:
        print(f"❌ Callback failed for job {job_id}: {e}")
