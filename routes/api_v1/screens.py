"""Screening session endpoints — org-scoped."""
import uuid
from flask import request
from routes.api_v1 import api_v1_bp
from services.api.auth import require_org, get_org_context
from services.api.responses import api_ok, api_error


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

    _save_session(ctx.org_id, session_id, session, sm)
    return api_ok({"state": sm.state.value, "outcome": outcome_data})


@api_v1_bp.route('/screens/<session_id>', methods=['GET'])
@require_org()
def get_screen(session_id):
    ctx = get_org_context()
    session_data = _load_session(ctx.org_id, session_id)
    if not session_data:
        return api_error("Session not found", 404)
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
