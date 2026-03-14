"""
Screening service: candidate screening call creation, scoring, and webhook delivery.
"""
import json
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests as http_requests

from config.clients import supabase_client, gpt_client


# ---------------------------------------------------------------------------
# Job creation
# ---------------------------------------------------------------------------

def create_screening_job(
    candidate_phone: str,
    candidate_name: str,
    role_title: str,
    company_name: str,
    questions: List[Dict[str, Any]],
    callback_url: Optional[str],
    source_candidate_id: Optional[str],
) -> Dict[str, Any]:
    """
    Create an outbound_call_job for a candidate screening call.

    The job is picked up automatically by the existing call_dispatcher worker.

    Returns dict with job_id, thread_id, interaction_id, status.
    """
    if not supabase_client:
        raise RuntimeError("Supabase client not available")

    now_iso = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()

    # Normalise phone to E.164
    phone = candidate_phone.strip()
    if not phone.startswith("+"):
        phone = "+" + phone.replace(" ", "").replace("-", "")

    # Thread
    thread_resp = supabase_client.table("threads").insert({
        "subject": f"Screening: {candidate_name} for {role_title}",
        "status": "open",
        "active": True,
        "created_at": now_iso,
        "updated_at": now_iso,
    }).execute()
    thread_id = thread_resp.data[0]["id"] if thread_resp.data else None

    # Interaction
    interaction_resp = supabase_client.table("interactions").insert({
        "thread_id": thread_id,
        "channel": "voice",
        "direction": "outbound",
        "provider": "twilio",
        "started_at": now_iso,
        "created_at": now_iso,
    }).execute()
    interaction_id = interaction_resp.data[0]["id"] if interaction_resp.data else None

    # Job
    job_resp = supabase_client.table("outbound_call_jobs").insert({
        "phone_e164": phone,
        "status": "queued",
        "thread_id": thread_id,
        "interaction_id": interaction_id,
        "created_at": now_iso,
        "updated_at": now_iso,
        "artifacts": {
            "call_type": "screening",
            "created_at": now_iso,
            "screening_context": {
                "candidate_name": candidate_name,
                "role_title": role_title,
                "company_name": company_name,
                "questions": questions,
                "callback_url": callback_url,
                "source_candidate_id": source_candidate_id,
            },
        },
    }).execute()
    job_id = job_resp.data[0]["id"] if job_resp.data else None

    return {
        "job_id": job_id,
        "thread_id": thread_id,
        "interaction_id": interaction_id,
        "status": "queued",
    }


# ---------------------------------------------------------------------------
# Post-call scoring
# ---------------------------------------------------------------------------

def score_screening_call(interaction_id: str, job_id: str) -> Optional[Dict[str, Any]]:
    """
    Score a completed screening call via OpenAI.

    Updates interactions with screening_scores and screening_recommendation.
    Fires webhook callback if configured.
    """
    if not supabase_client or not gpt_client:
        print("⚠️ Supabase or OpenAI client not available for scoring")
        return None

    try:
        # Load job
        job_resp = (
            supabase_client.table("outbound_call_jobs")
            .select("*")
            .eq("id", job_id)
            .limit(1)
            .execute()
        )
        if not job_resp.data:
            print(f"⚠️ No job found for scoring: job_id={job_id}")
            return None

        job = job_resp.data[0]
        artifacts = job.get("artifacts", {}) or {}
        ctx = artifacts.get("screening_context", {}) or {}

        role_title = ctx.get("role_title", "the role")
        candidate_name = ctx.get("candidate_name", "the candidate")
        company_name = ctx.get("company_name", "the company")
        questions = ctx.get("questions", [])
        callback_url = ctx.get("callback_url")
        source_candidate_id = ctx.get("source_candidate_id")
        call_duration_seconds = int(artifacts.get("call_duration") or 0)
        recording_url = artifacts.get("recording_url")
        call_status_raw = artifacts.get("call_status", "completed")

        # Build transcript
        turns_resp = (
            supabase_client.table("interaction_turns")
            .select("speaker, text, turn_sequence")
            .eq("interaction_id", interaction_id)
            .order("turn_sequence", desc=False)
            .execute()
        )
        turns = turns_resp.data or []
        transcript_lines = []
        for t in turns:
            speaker = (t.get("speaker") or "").capitalize()
            text = (t.get("text") or "").strip()
            if text:
                transcript_lines.append(f"{speaker}: {text}")
        transcript = "\n".join(transcript_lines)

        if not transcript:
            print(f"⚠️ Empty transcript for interaction {interaction_id}, skipping scoring")
            return None

        # Score via OpenAI
        questions_json = json.dumps(questions, indent=2)
        scoring_prompt = f"""You are scoring a candidate screening call for the role of {role_title} at {company_name}.

Candidate: {candidate_name}

Screening questions:
{questions_json}

Full call transcript:
{transcript}

For each screening question that was addressed in the call, extract:
- "question": the question text
- "competency": the competency being assessed (use value from questions list if provided, otherwise infer)
- "weight": the weight (use value from questions list if provided, otherwise 1.0)
- "response_summary": 2-3 sentence summary of the candidate's response
- "score": integer 1-5 (1=Poor, 2=Below Expected, 3=Meets Expected, 4=Strong, 5=Exceptional)

Also provide:
- "overall_score": weighted average of individual scores, as a float
- "recommendation": one of: "strong_proceed", "proceed", "hold", "reject"
- "candidate_summary": 2-3 sentence overall candidate summary

Respond ONLY with valid JSON matching this exact structure:
{{
  "scores": [
    {{
      "question": "...",
      "competency": "...",
      "weight": 1.0,
      "response_summary": "...",
      "score": 4
    }}
  ],
  "overall_score": 3.5,
  "recommendation": "proceed",
  "candidate_summary": "..."
}}"""

        completion = gpt_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": scoring_prompt}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        result = json.loads(completion.choices[0].message.content)

        scores = result.get("scores", [])
        overall_score = float(result.get("overall_score", 0))
        recommendation = result.get("recommendation", "hold")
        candidate_summary = result.get("candidate_summary", "")

        # Persist to interaction
        now_iso = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()
        supabase_client.table("interactions").update({
            "screening_scores": scores,
            "screening_recommendation": recommendation,
        }).eq("id", interaction_id).execute()

        print(
            f"✅ Scored screening: interaction_id={interaction_id}, "
            f"overall_score={overall_score}, recommendation={recommendation}"
        )

        # Fire callback
        if callback_url:
            call_status = "succeeded" if call_status_raw == "completed" else call_status_raw
            _fire_callback(
                callback_url=callback_url,
                payload={
                    "source_candidate_id": source_candidate_id,
                    "transcript": transcript,
                    "scores": scores,
                    "overall_score": overall_score,
                    "recommendation": recommendation,
                    "candidate_summary": candidate_summary,
                    "call_duration_seconds": call_duration_seconds,
                    "recording_url": recording_url,
                    "call_status": call_status,
                },
                job_id=job_id,
            )

        return {
            "scores": scores,
            "overall_score": overall_score,
            "recommendation": recommendation,
            "candidate_summary": candidate_summary,
        }

    except Exception as e:
        print(f"❌ Error scoring screening call: {e}")
        import traceback
        traceback.print_exc()
        return None


def score_screening_call_async(interaction_id: str, job_id: str):
    """Fire scoring in a background daemon thread (call from /voice/status)."""
    t = threading.Thread(
        target=score_screening_call,
        args=(interaction_id, job_id),
        daemon=True,
    )
    t.start()


# ---------------------------------------------------------------------------
# Webhook delivery
# ---------------------------------------------------------------------------

def _fire_callback(callback_url: str, payload: Dict[str, Any], job_id: Optional[str] = None):
    """POST results to the callback URL. Errors are logged; not re-raised."""
    try:
        resp = http_requests.post(callback_url, json=payload, timeout=15)
        if resp.status_code >= 400:
            print(
                f"⚠️ Callback returned {resp.status_code} for job {job_id}: "
                f"{resp.text[:200]}"
            )
        else:
            print(f"✅ Callback delivered to {callback_url} (status {resp.status_code})")
    except Exception as e:
        print(f"❌ Callback failed for job {job_id} ({callback_url}): {e}")
        if job_id and supabase_client:
            try:
                existing = (
                    supabase_client.table("outbound_call_jobs")
                    .select("artifacts")
                    .eq("id", job_id)
                    .limit(1)
                    .execute()
                )
                if existing.data:
                    arts = (existing.data[0] or {}).get("artifacts", {}) or {}
                    arts["callback_error"] = {"error": str(e), "url": callback_url}
                    supabase_client.table("outbound_call_jobs").update({
                        "artifacts": arts,
                        "updated_at": datetime.utcnow().replace(tzinfo=timezone.utc).isoformat(),
                    }).eq("id", job_id).execute()
            except Exception as inner_e:
                print(f"⚠️ Could not store callback error in artifacts: {inner_e}")
