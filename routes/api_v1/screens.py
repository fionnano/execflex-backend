"""Screening session endpoints — org-scoped."""
import uuid
from flask import request
from routes.api_v1 import api_v1_bp
from services.api.auth import require_org, get_org_context
from services.api.responses import api_ok, api_error

# Default question set for Aidan phone screenings started from the console.
# Mirrors the defaults the old-app modal sent to POST /screening.
DEFAULT_PHONE_QUESTIONS = [
    {"question": "Tell me about your current role and key responsibilities.", "competency": "Experience", "weight": 1.0},
    {"question": "What are you looking for in your next opportunity?", "competency": "Motivation", "weight": 1.0},
    {"question": "Describe a significant achievement in the last two years.", "competency": "Impact", "weight": 1.0},
]


@api_v1_bp.route('/screens', methods=['GET'])
@require_org()
def list_screens():
    """List screening sessions for the org, optionally filtered by candidate.

    Phone (Aidan) sessions are read-through synced: if the linked outbound
    call has completed since the last read, its results are copied into the
    session row before it is returned.
    """
    ctx = get_org_context()
    from config.clients import supabase_client
    query = supabase_client.table("screening_sessions") \
        .select("*") \
        .eq("organization_id", ctx.org_id) \
        .order("created_at", desc=True) \
        .limit(100)
    candidate_id = request.args.get("candidate_id")
    if candidate_id:
        query = query.eq("candidate_id", candidate_id)
    result = query.execute()
    sessions = [_sync_phone_session(ctx.org_id, row) for row in (result.data or [])]
    return api_ok({"sessions": sessions, "total": len(sessions)})


@api_v1_bp.route('/screens/phone', methods=['POST'])
@require_org(allowed_roles=["owner", "recruiter"])
def create_phone_screen():
    """Start an Aidan outbound phone screening for a console candidate.

    Wires the proven AI Dan call path (create_screening_job → dispatcher →
    Twilio → OpenAI Realtime) to an org-scoped screening_sessions row so the
    completed call surfaces in the console's screening review.
    """
    ctx = get_org_context()
    data = request.get_json() or {}

    candidate_id = data.get("candidate_id")
    phone = (data.get("phone") or "").strip()
    if not candidate_id:
        return api_error("candidate_id is required", 400)
    if not phone:
        return api_error("phone is required", 400)
    normalized = phone if phone.startswith("+") else "+" + phone.replace(" ", "").replace("-", "")
    digits = normalized[1:]
    if not (digits.isdigit() and 10 <= len(digits) <= 15):
        return api_error("phone must be E.164 (e.g. +353861234567)", 400)

    from config.clients import supabase_client
    cand = supabase_client.table("people_profiles") \
        .select("id, first_name, last_name") \
        .eq("id", candidate_id) \
        .eq("organization_id", ctx.org_id) \
        .execute()
    if not cand.data:
        return api_error("Candidate not found", 404)
    row = cand.data[0]
    candidate_name = (data.get("candidate_name")
                      or f"{row.get('first_name') or ''} {row.get('last_name') or ''}".strip()
                      or "Candidate")

    # Same gates as the legacy /screening route: sliding-window rate limit
    # plus subscription-tier quota.
    from routes.screening import _check_screening_rate
    if not _check_screening_rate(ctx.user_id):
        return api_error("Rate limit exceeded: max 10 screening calls per hour", 429)
    from services.billing_service import check_quota
    allowed, quota_msg = check_quota(ctx.user_id, "screenings_done")
    if not allowed:
        return api_error(quota_msg, 403)

    opportunity_id = data.get("opportunity_id")
    role_title = data.get("role_title") or "General Screening"
    company_name = data.get("company_name")
    if not company_name:
        try:
            org = supabase_client.table("organizations").select("name").eq("id", ctx.org_id).execute()
            company_name = (org.data[0].get("name") if org.data else None) or "ainm Search"
        except Exception:
            company_name = "ainm Search"

    questions = data.get("questions") or DEFAULT_PHONE_QUESTIONS
    if not isinstance(questions, list) or not questions:
        return api_error("questions must be a non-empty array", 400)

    from services.screening_service import create_screening_job
    try:
        job = create_screening_job(
            candidate_phone=normalized,
            candidate_name=candidate_name,
            role_title=role_title,
            company_name=company_name,
            questions=questions,
            callback_url=None,
            source_candidate_id=candidate_id,
            purpose="screening",
            user_id=ctx.user_id,
            role_id=opportunity_id,
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        return api_error(f"Could not start call: {e}", 500)

    session_id = str(uuid.uuid4())
    session_questions = [
        {"id": f"phone_q{i}", "text": q.get("question", ""),
         "category": q.get("competency", "general"), "weight": q.get("weight", 1.0)}
        for i, q in enumerate(questions)
    ]
    supabase_client.table("screening_sessions").insert({
        "id": session_id,
        "organization_id": ctx.org_id,
        "session_type": "candidate",
        "candidate_id": candidate_id,
        "opportunity_id": opportunity_id,
        "state": "in_progress",
        # Aidan asks for verbal consent at the start of the call; recorded
        # in the bias-audit trail, not here.
        "consent_given": False,
        "questions": session_questions,
        "metadata": {
            "channel": "aidan_phone",
            "outbound_call_job_id": job.get("job_id"),
            "interaction_id": job.get("interaction_id"),
            "candidate_name": candidate_name,
            "role_title": role_title,
        },
    }).execute()

    from services.compliance.decision_logger import log_activity
    log_activity(ctx.org_id, "candidate", candidate_id, "screening_started",
                 ctx.user_id, f"Aidan phone screening started ({role_title})")

    return api_ok({
        "session_id": session_id,
        "job_id": job.get("job_id"),
        "status": "queued",
    }, 201)


@api_v1_bp.route('/screens/<session_id>/call-status', methods=['GET'])
@require_org()
def phone_screen_status(session_id):
    """Poll the live status of an Aidan phone screening (org-scoped).

    Returns the same payload shape as the proven legacy
    GET /screening/<job_id>/status, plus session_id, and syncs completed
    call results into the screening session row.
    """
    ctx = get_org_context()
    session_data = _load_session(ctx.org_id, session_id)
    if not session_data:
        return api_error("Session not found", 404)

    job_id = (session_data.get("metadata") or {}).get("outbound_call_job_id")
    if not job_id:
        return api_error("Not a phone screening session", 400)

    from services.screening_service import get_screening_status
    status = get_screening_status(job_id)
    if status is None:
        return api_error("Call job not found", 404)

    _apply_phone_sync(ctx.org_id, session_data, status)
    status["session_id"] = session_id
    return api_ok(status)


def _sync_phone_session(org_id, session_data):
    """Read-through sync for a phone-linked session; returns the fresh row."""
    try:
        metadata = session_data.get("metadata") or {}
        job_id = metadata.get("outbound_call_job_id")
        if not job_id or session_data.get("state") in ("complete", "handoff"):
            return session_data
        from services.screening_service import get_screening_status
        status = get_screening_status(job_id)
        if status is None:
            return session_data
        updated = _apply_phone_sync(org_id, session_data, status)
        return updated or session_data
    except Exception as e:
        print(f"[PhoneScreenSync] sync failed for session {session_data.get('id')}: {e}", flush=True)
        return session_data


def _apply_phone_sync(org_id, session_data, status):
    """Copy completed Aidan call results into the console screening session.

    Answers store both the state-machine keys (question_id/response_text) and
    the console keys (question_index/text) so every reader renders. Legacy
    scores are 1-5; the console renders /10, so scores are scaled ×2.
    Returns the updated row dict, or None if no update was applied.
    """
    if session_data.get("state") in ("complete", "handoff"):
        return None
    call_state = status.get("status")

    from config.clients import supabase_client
    from datetime import datetime, timezone

    if call_state in ("failed", "no_answer"):
        metadata = dict(session_data.get("metadata") or {})
        metadata["call_status"] = call_state
        updates = {"state": "handoff", "handoff_reason": f"call_{call_state}", "metadata": metadata}
        supabase_client.table("screening_sessions").update(updates) \
            .eq("id", session_data["id"]).eq("organization_id", org_id).execute()
        return {**session_data, **updates}

    if call_state != "completed":
        return None

    scores = status.get("scores") or []
    extraction = status.get("extraction_status")
    if not scores and extraction not in ("complete", "failed"):
        # Call over but post-call analysis still running — sync next read.
        return None

    answers = []
    for i, s in enumerate(scores):
        raw = s.get("score")
        scaled = max(0, min(10, round(float(raw) * 2))) if isinstance(raw, (int, float)) else 0
        answers.append({
            "question_id": f"phone_q{i}",
            "question_index": i,
            "response_text": s.get("response_summary", ""),
            "text": s.get("response_summary", ""),
            "score": scaled,
        })

    numeric = [s["score"] for s in scores if isinstance(s.get("score"), (int, float))]
    overall_5 = (sum(numeric) / len(numeric)) if numeric else None
    recommendation = status.get("recommendation") or "hold"
    profile = status.get("candidate_profile") or {}
    summary = (profile.get("summary") or profile.get("candidate_summary")
               or f"Aidan phone screening — recommendation: {recommendation}")

    metadata = dict(session_data.get("metadata") or {})
    metadata["call_status"] = "completed"
    if status.get("transcript"):
        metadata["transcript"] = status["transcript"]
    if profile:
        metadata["candidate_extraction"] = profile

    updates = {
        "state": "complete",
        "answers": answers,
        "outcome": {
            "recommendation": recommendation,
            "score": round(overall_5 / 5.0, 2) if overall_5 is not None else None,
            "summary": summary,
        },
        "metadata": metadata,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    supabase_client.table("screening_sessions").update(updates) \
        .eq("id", session_data["id"]).eq("organization_id", org_id).execute()

    from services.compliance.decision_logger import log_decision
    log_decision(
        org_id=org_id,
        decision_type="screening_score",
        candidate_id=session_data.get("candidate_id"),
        opportunity_id=session_data.get("opportunity_id"),
        inputs={"channel": "aidan_phone", "job_id": metadata.get("outbound_call_job_id")},
        model_used="aidan_phone_v1",
        score=round(overall_5 / 5.0, 2) if overall_5 is not None else None,
        explanation=summary,
    )
    return {**session_data, **updates}


@api_v1_bp.route('/screens', methods=['POST'])
@require_org(allowed_roles=["owner", "recruiter"])
def create_screen():
    """Start a new screening session for a candidate or client."""
    ctx = get_org_context()
    data = request.get_json()
    if not data:
        return api_error("Request body required", 400)

    session_type = data.get("session_type", "candidate")
    if session_type not in ("candidate", "client"):
        return api_error("session_type must be 'candidate' or 'client'", 400)

    from services.screening.models import ScreeningSession, ScreeningSessionType
    from services.screening.state_machine import ScreeningStateMachine

    st = ScreeningSessionType.CANDIDATE if session_type == "candidate" else ScreeningSessionType.CLIENT
    session = ScreeningSession(
        session_id=str(uuid.uuid4()),
        session_type=st,
        candidate_id=data.get("candidate_id"),
        client_id=data.get("client_id"),
        role_id=data.get("opportunity_id"),
    )
    sm = ScreeningStateMachine(session)
    greeting = sm.start()

    from config.clients import supabase_client
    row = {
        "id": session.session_id,
        "organization_id": ctx.org_id,
        "session_type": session_type,
        "candidate_id": data.get("candidate_id"),
        "client_id": data.get("client_id"),
        "opportunity_id": data.get("opportunity_id"),
        "state": sm.state.value,
        "questions": [{"id": q.id, "text": q.text, "category": q.category, "weight": q.weight}
                      for q in session.questions],
        "transitions": sm.transitions,
    }
    supabase_client.table("screening_sessions").insert(row).execute()

    from services.compliance.decision_logger import log_activity
    log_activity(ctx.org_id, "candidate", data.get("candidate_id") or data.get("client_id"),
                 "screening_started", ctx.user_id, f"Screening session created ({session_type})")

    return api_ok({
        "session_id": session.session_id,
        "state": sm.state.value,
        "greeting": greeting,
        "questions": [{"id": q.id, "text": q.text} for q in session.questions],
    }, 201)


@api_v1_bp.route('/screens/<session_id>/consent', methods=['POST'])
@require_org()
def give_consent(session_id):
    ctx = get_org_context()
    data = request.get_json()
    consented = data.get("consented", False) if data else False

    session_data = _load_session(ctx.org_id, session_id)
    if not session_data:
        return api_error("Session not found", 404)

    session, sm = _restore_state_machine(session_data)
    response = sm.give_consent(consented)

    _save_session(ctx.org_id, session_id, session, sm)
    return api_ok({"state": sm.state.value, "message": response})


@api_v1_bp.route('/screens/<session_id>/answer', methods=['POST'])
@require_org()
def submit_answer(session_id):
    ctx = get_org_context()
    data = request.get_json()
    response_text = data.get("response", "") if data else ""

    session_data = _load_session(ctx.org_id, session_id)
    if not session_data:
        return api_error("Session not found", 404)

    session, sm = _restore_state_machine(session_data)
    reply = sm.answer(response_text)

    _save_session(ctx.org_id, session_id, session, sm)
    return api_ok({
        "state": sm.state.value,
        "message": reply,
        "progress": session.progress,
    })


@api_v1_bp.route('/screens/<session_id>/score', methods=['POST'])
@require_org(allowed_roles=["owner", "recruiter"])
def score_screen(session_id):
    ctx = get_org_context()
    session_data = _load_session(ctx.org_id, session_id)
    if not session_data:
        return api_error("Session not found", 404)

    session, sm = _restore_state_machine(session_data)

    if session_data["session_type"] == "client":
        brief = sm.build_brief()
        outcome_data = {
            "role_title": brief.role_title,
            "requirements": brief.requirements_must_have,
            "nice_to_have": brief.requirements_nice_to_have,
            "timeline": brief.timeline,
            "budget": brief.budget_range,
        }
    else:
        outcome = sm.score()
        outcome_data = {
            "recommendation": outcome.recommendation,
            "score": outcome.overall_score,
            "summary": outcome.summary,
            "extracted_facts": outcome.extracted_facts,
        }

        from services.compliance.decision_logger import log_decision
        log_decision(
            org_id=ctx.org_id,
            decision_type="screening_score",
            candidate_id=session_data.get("candidate_id"),
            opportunity_id=session_data.get("opportunity_id"),
            inputs={"answers": [a.response_text for a in session.answers]},
            model_used="heuristic_v1",
            score=outcome.overall_score,
            explanation=outcome.summary,
        )

        from services.ai.agent_service import summarise_screening
        transcript = [
            {"question": q.text, "answer": a.response_text}
            for q, a in zip(session.questions, session.answers)
        ]
        candidate_name = session_data.get("candidate_id", "Unknown")
        role_title = session_data.get("opportunity_id", "Unknown Role")
        ai_summary = summarise_screening(
            candidate_name=candidate_name,
            role_title=role_title,
            transcript=transcript,
            screening_score=outcome.overall_score,
        )
        if ai_summary:
            outcome_data["ai_summary"] = ai_summary
            log_decision(
                org_id=ctx.org_id,
                decision_type="ai_screening_summary",
                candidate_id=session_data.get("candidate_id"),
                opportunity_id=session_data.get("opportunity_id"),
                model_used="claude-sonnet-4-5",
                score=outcome.overall_score,
                explanation=ai_summary.get("one_line_summary", ""),
            )

    _save_session(ctx.org_id, session_id, session, sm)
    return api_ok({"state": sm.state.value, "outcome": outcome_data})


@api_v1_bp.route('/screens/<session_id>', methods=['GET'])
@require_org()
def get_screen(session_id):
    ctx = get_org_context()
    session_data = _load_session(ctx.org_id, session_id)
    if not session_data:
        return api_error("Session not found", 404)
    session_data = _sync_phone_session(ctx.org_id, session_data)
    return api_ok(session_data)


def _load_session(org_id, session_id):
    from config.clients import supabase_client
    result = supabase_client.table("screening_sessions") \
        .select("*") \
        .eq("id", session_id) \
        .eq("organization_id", org_id) \
        .execute()
    return result.data[0] if result.data else None


def _restore_state_machine(data):
    from services.screening.models import (
        ScreeningSession, ScreeningSessionType, Question, Answer
    )
    from services.screening.state_machine import ScreeningStateMachine, ScreeningState

    st = ScreeningSessionType.CANDIDATE if data["session_type"] == "candidate" else ScreeningSessionType.CLIENT
    questions = [Question(id=q["id"], text=q["text"], category=q["category"],
                          weight=q.get("weight", 1.0))
                 for q in (data.get("questions") or [])]
    answers = [Answer(question_id=a["question_id"], response_text=a["response_text"])
               for a in (data.get("answers") or [])]

    session = ScreeningSession(
        session_id=data["id"],
        session_type=st,
        questions=questions,
        answers=answers,
        current_question_index=len(answers),
        consent_given=data.get("consent_given", False),
    )
    sm = ScreeningStateMachine(session)
    sm._state = ScreeningState(data["state"])
    sm._transition_log = data.get("transitions") or []
    return session, sm


def _save_session(org_id, session_id, session, sm):
    from config.clients import supabase_client
    updates = {
        "state": sm.state.value,
        "consent_given": session.consent_given,
        "answers": [{"question_id": a.question_id, "response_text": a.response_text,
                      "score": a.score, "extracted_facts": a.extracted_facts}
                     for a in session.answers],
        "transitions": sm.transitions,
        "handoff_reason": session.handoff_reason,
    }
    if session.outcome:
        updates["outcome"] = {
            "recommendation": session.outcome.recommendation,
            "score": session.outcome.overall_score,
            "summary": session.outcome.summary,
        }
    if session.brief:
        updates["brief"] = {
            "role_title": session.brief.role_title,
            "requirements": session.brief.requirements_must_have,
        }
    if sm.state.value in ("complete", "handoff"):
        from datetime import datetime, timezone
        updates["completed_at"] = datetime.now(timezone.utc).isoformat()

    supabase_client.table("screening_sessions") \
        .update(updates) \
        .eq("id", session_id) \
        .eq("organization_id", org_id) \
        .execute()
