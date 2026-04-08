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
    purpose: Optional[str] = None,
    user_id: Optional[str] = None,
    role_id: Optional[str] = None,
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

    # Screening calls have no platform user — skip thread creation
    thread_id = None

    # Interaction
    interaction_resp = supabase_client.table("interactions").insert({
        "channel": "voice",
        "direction": "outbound",
        "provider": "twilio",
        "started_at": now_iso,
        "created_at": now_iso,
    }).execute()
    interaction_id = interaction_resp.data[0]["id"] if interaction_resp.data else None

    # Job
    job_payload = {
        "phone_e164": phone,
        "status": "queued",
        "thread_id": thread_id,
        "interaction_id": interaction_id,
        "created_at": now_iso,
        "updated_at": now_iso,
        "artifacts": {
            "call_type": purpose if purpose in ("candidate_chat", "employer_brief", "talent_network") else "screening",
            "created_at": now_iso,
            "screening_context": {
                "candidate_name": candidate_name,
                "role_title": role_title,
                "company_name": company_name,
                "questions": questions,
                "callback_url": callback_url,
                "source_candidate_id": source_candidate_id,
                "role_id": role_id,
                # Aidan asks for verbal consent at the start of every
                # call; the bias-audit logger captures the response.
                "consent_requested": True,
            },
        },
    }
    if user_id:
        job_payload["user_id"] = user_id
    job_resp = supabase_client.table("outbound_call_jobs").insert(job_payload).execute()
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

        # Guard: don't score incomplete calls (too short)
        user_turns = [t for t in turns if (t.get("speaker") or "").lower() == "user"]
        if len(user_turns) < 3 or call_duration_seconds < 60:
            print(f"⚠️ Incomplete call: {len(user_turns)} user turns, {call_duration_seconds}s duration — skipping scoring")
            # Fire callback with incomplete status
            if callback_url:
                _fire_callback(
                    callback_url=callback_url,
                    payload={
                        "source_candidate_id": source_candidate_id,
                        "transcript": transcript,
                        "scores": [],
                        "overall_score": None,
                        "recommendation": "incomplete",
                        "candidate_summary": "Call ended early — insufficient data for scoring. Recommend rescheduling.",
                        "call_duration_seconds": call_duration_seconds,
                        "call_status": "incomplete",
                    },
                    job_id=job_id,
                )
            return None

        # Score via OpenAI — EU AI Act compliant competency-only rubric
        questions_json = json.dumps(questions, indent=2)
        scoring_prompt = f"""You are an impartial scoring engine for a candidate screening call. Score ONLY job-relevant competencies demonstrated in the answers. You must NOT consider or be influenced by any protected characteristics.

Role: {role_title} at {company_name}

SCORING RULES — CRITICAL:
1. Score ONLY on the competency each question assesses. Nothing else.
2. Base scores EXCLUSIVELY on the SUBSTANCE — relevant experience, knowledge, and examples provided.
3. Do NOT consider or penalise: accent, fluency, grammar, filler words, pauses, speaking speed, confidence level, communication style, or answer structure.
4. Do NOT infer or consider: age, gender, race, ethnicity, nationality, disability, religion, sexual orientation, or any protected characteristic — even if voluntarily mentioned.
5. If a question was not answered (e.g. call ended early), mark as "not_assessed" with score null.
6. A score of 3 means the answer meets expectations — it is the baseline, not mediocre.

SCORING RUBRIC:
1 = No relevant evidence: No relevant experience, knowledge, or examples for this competency.
2 = Limited evidence: Some awareness but lacked specific examples or depth.
3 = Meets expectations: Relevant experience and at least one concrete example.
4 = Strong evidence: Multiple relevant examples with clear impact and depth.
5 = Exceptional evidence: Outstanding expertise with compelling, detailed examples.

Screening questions:
{questions_json}

Full call transcript:
{transcript}

For each question, extract:
- "question": the question text
- "competency": the competency assessed (from questions list if provided, otherwise infer)
- "weight": the weight (from questions list if provided, otherwise 1.0)
- "response_summary": 2-3 sentence factual summary of the answer (substance only — no commentary on delivery style)
- "score": integer 1-5 per rubric, or null if not assessed
- "score_justification": 1 sentence explaining which rubric level applies, referencing specific answer content

Also provide:
- "overall_score": weighted average of scored questions (exclude not_assessed), float rounded to 1 decimal
- "recommendation": one of "strong_proceed", "proceed", "hold", "reject" — based SOLELY on competency scores
- "candidate_summary": 2-3 sentence summary of demonstrated competencies only. Do NOT reference communication style, accent, or personal characteristics.
- "bias_flags": list of any potential bias concerns detected (empty list if none). Examples: "candidate voluntarily disclosed age", "question was skipped"

BIAS SELF-CHECK: Before finalising, review — would you give identical scores if this candidate had a different name, accent, or communication style but gave identical answers? If not, revise.

Respond ONLY with valid JSON:
{{
  "scores": [
    {{
      "question": "...",
      "competency": "...",
      "weight": 1.0,
      "response_summary": "...",
      "score": 4,
      "score_justification": "..."
    }}
  ],
  "overall_score": 3.5,
  "recommendation": "proceed",
  "candidate_summary": "...",
  "bias_flags": []
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
        bias_flags = result.get("bias_flags", [])

        # Persist to interaction + generate candidate portal token
        import uuid
        candidate_token = str(uuid.uuid4())
        now_iso = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()
        supabase_client.table("interactions").update({
            "screening_scores": scores,
            "screening_recommendation": recommendation,
            "candidate_token": candidate_token,
        }).eq("id", interaction_id).execute()
        print(f"[Screening] Generated candidate_token={candidate_token} for interaction={interaction_id}", flush=True)

        print(
            f"✅ Scored screening: interaction_id={interaction_id}, "
            f"overall_score={overall_score}, recommendation={recommendation}, "
            f"bias_flags={bias_flags}"
        )

        # PostHog: screening_completed
        try:
            from services.analytics_service import track
            track("screening_completed", source_candidate_id, {
                "interaction_id": interaction_id,
                "job_id": job_id,
                "role_id": ctx.get("role_id"),
                "role_title": role_title,
                "recommendation": recommendation,
                "overall_score": overall_score,
                "call_duration": call_duration_seconds,
            })
        except Exception as e:
            print(f"[Analytics] screening_completed failed: {e}", flush=True)

        # Log bias audit record + capture inferred consent
        consented = _log_bias_audit(
            interaction_id=interaction_id,
            job_id=job_id,
            role_id=ctx.get("role_id"),
            role_title=role_title,
            company_name=company_name,
            questions=questions,
            scores=scores,
            overall_score=overall_score,
            recommendation=recommendation,
            bias_flags=bias_flags,
            transcript=transcript,
            call_duration_seconds=call_duration_seconds,
        )

        # FIX 7: If the candidate verbally consented during the AI
        # disclosure step, flip consent_given on their people_profiles
        # row. The lookup key is source_candidate_id, which for signup-
        # path candidates is their auth user_id and matches
        # people_profiles.user_id.
        if consented is True and source_candidate_id:
            try:
                supabase_client.table("people_profiles").update({
                    "consent_given": True,
                    "consent_given_at": now_iso,
                }).eq("user_id", source_candidate_id).execute()
                print(
                    f"[Consent] Set consent_given=True on people_profiles "
                    f"for user_id={source_candidate_id}",
                    flush=True,
                )
            except Exception as e:
                print(
                    f"[Consent] Failed to update people_profiles for "
                    f"user_id={source_candidate_id}: {e}",
                    flush=True,
                )

        # Admin notification — fire after scoring + bias audit have
        # landed so the email has the final recommendation. Best-
        # effort; failures never affect scoring or the callback.
        try:
            # Top 2 competencies by score
            numeric_scores = [
                (s.get("competency") or s.get("question") or "?", float(s.get("score") or 0))
                for s in scores
                if isinstance(s.get("score"), (int, float))
            ]
            numeric_scores.sort(key=lambda x: x[1], reverse=True)
            top_two = numeric_scores[:2]

            from modules.email_sender import send_screening_complete_admin_alert
            send_screening_complete_admin_alert(
                candidate_name=candidate_name,
                role_title=role_title,
                company_name=company_name,
                recommendation=recommendation,
                overall_score=overall_score,
                top_competencies=top_two,
                job_id=job_id,
            )
        except Exception as e:
            print(f"[NOTIFY] screening-complete alert failed: {e}", flush=True)

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
                    "bias_flags": bias_flags,
                    "call_duration_seconds": call_duration_seconds,
                    "recording_url": recording_url,
                    "call_status": call_status,
                    "candidate_token": candidate_token,
                    "candidate_portal_url": f"https://execflex.ai/my-screening?token={candidate_token}",
                },
                job_id=job_id,
            )

        return {
            "scores": scores,
            "overall_score": overall_score,
            "recommendation": recommendation,
            "candidate_summary": candidate_summary,
            "bias_flags": bias_flags,
            "candidate_token": candidate_token,
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
# Bias audit logging (EU AI Act compliance)
# ---------------------------------------------------------------------------

def _log_bias_audit(
    interaction_id: str,
    job_id: str,
    role_id: Optional[str],
    role_title: str,
    company_name: str,
    questions: list,
    scores: list,
    overall_score: float,
    recommendation: str,
    bias_flags: list,
    transcript: str,
    call_duration_seconds: int,
) -> Optional[bool]:
    """
    Log bias audit record for EU AI Act compliance.

    Returns the inferred `candidate_consented` value (True / False / None)
    so the caller can propagate consent to people_profiles.
    """
    try:
        import statistics

        questions_expected = len(questions)
        questions_asked = len([s for s in scores if s.get("score") is not None])
        questions_skipped = questions_expected - questions_asked

        # Check question order preserved
        score_questions = [s.get("question", "").lower().strip() for s in scores if s.get("score") is not None]
        expected_questions = []
        for q in questions:
            if isinstance(q, dict):
                expected_questions.append(q.get("question", "").lower().strip())
            else:
                expected_questions.append(str(q).lower().strip())
        order_preserved = True
        last_idx = -1
        for sq in score_questions:
            for idx, eq in enumerate(expected_questions):
                if sq in eq or eq in sq:
                    if idx < last_idx:
                        order_preserved = False
                    last_idx = idx
                    break

        # Score standard deviation
        numeric_scores = [s["score"] for s in scores if isinstance(s.get("score"), (int, float))]
        std_dev = round(statistics.stdev(numeric_scores), 2) if len(numeric_scores) >= 2 else 0.0

        # AI disclosure check
        transcript_lower = transcript.lower()
        ai_disclosure = (
            "artificial intelligence" in transcript_lower
            or "i am an ai" in transcript_lower
            or "ai screening assistant" in transcript_lower
        )

        # Consent check
        consented = None
        if ai_disclosure:
            consented = any(
                phrase in transcript_lower
                for phrase in ["yes", "sure", "sounds good", "ok", "okay", "go ahead", "yeah"]
            )

        supabase_client.table("screening_bias_audit").insert({
            "interaction_id": interaction_id,
            "job_id": job_id,
            "role_id": role_id,
            "role_title": role_title,
            "company_name": company_name,
            "questions_asked": questions_asked,
            "questions_expected": questions_expected,
            "questions_skipped": questions_skipped,
            "question_order_preserved": order_preserved,
            "overall_score": overall_score,
            "recommendation": recommendation,
            "score_std_deviation": std_dev,
            "bias_flags": bias_flags,
            "ai_disclosure_given": ai_disclosure,
            "candidate_consented": consented,
            "scoring_model": "gpt-4o",
            "prompt_version": "v1_eu_ai_act",
            "transcript_length": len(transcript),
            "call_duration_seconds": call_duration_seconds,
        }).execute()

        print(f"[BiasAudit] Logged: interaction={interaction_id}, questions={questions_asked}/{questions_expected}, disclosure={ai_disclosure}, consent={consented}", flush=True)
        return consented
    except Exception as e:
        print(f"[BiasAudit] ERROR: {e}", flush=True)
        return None


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
