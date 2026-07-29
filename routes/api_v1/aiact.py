"""ainm AI Act Check API — EU AI Act readiness assessment for a company's AI use.

A NEW org-scoped product surface (see DECISIONS.md D-18), separate from the
console's /compliance decisions surface. A company assesses its own AI use
(hiring/HR emphasis) and receives a risk tier, the obligations it's subject to,
gaps, and a readiness score with an explainable rationale.

This is a readiness / decision-support tool, NOT legal advice — the disclaimer is
returned with every result and the question set.

Routes (all under /api/v1/aiact, org-scoped):
  GET    /questions                     the staged question set + disclaimer
  POST   /assessments                   create a (draft) assessment
  GET    /assessments                   list YOUR org's assessments (tenant-scoped)
  GET    /assessments/<id>              get one (tenant-scoped)
  PATCH  /assessments/<id>              save answers / rename
  POST   /assessments/<id>/score        run the engine → persisted result (rate-limited)
  DELETE /assessments/<id>              GDPR: erase the assessment
  GET    /assessments/<id>/export       GDPR: export the assessment
"""
import threading
import time

from flask import request

from routes.api_v1 import api_v1_bp
from services.api.auth import require_org, get_org_context
from services.api.responses import api_ok, api_error
from services.aiact import store
from services.aiact import validation as V
from services.aiact.constants import (
    DISCLAIMER, SCORE_IP_LIMIT, SCORE_IP_WINDOW_S, SCORE_ORG_LIMIT, SCORE_ORG_WINDOW_S,
)


# ── Rate limiting (process-local sliding windows) ────────────────────────────

_ip_buckets: dict = {}
_org_buckets: dict = {}
_rl_lock = threading.Lock()


def _client_ip() -> str:
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _rate_ok(bucket: dict, key: str, limit: int, window_s: int) -> bool:
    now = time.time()
    cutoff = now - window_s
    with _rl_lock:
        ts = [t for t in bucket.get(key, []) if t > cutoff]
        if len(ts) >= limit:
            bucket[key] = ts
            return False
        ts.append(now)
        bucket[key] = ts
        return True


# ── Question set ─────────────────────────────────────────────────────────────

@api_v1_bp.route('/aiact/questions', methods=['GET'])
@require_org()
def aiact_questions():
    from agentic_core.agents.compliance import question_set_dict
    data = question_set_dict()
    data["disclaimer"] = DISCLAIMER
    return api_ok(data)


# ── Assessments (create / list / get / update) ───────────────────────────────

@api_v1_bp.route('/aiact/assessments', methods=['POST'])
@require_org()
def aiact_create_assessment():
    ctx = get_org_context()
    data = request.get_json() or {}
    try:
        system_name = V.clean_system_name(data.get("system_name"))
        answers = V.validate_answers(data.get("answers") or {})
    except V.ValidationError as e:
        return api_error(str(e), 400)
    a = store.create_assessment(
        org_id=ctx.org_id, actor_id=ctx.user_id,
        system_name=system_name, answers=answers,
    )
    return api_ok(a, 201)


@api_v1_bp.route('/aiact/assessments', methods=['GET'])
@require_org()
def aiact_list_assessments():
    ctx = get_org_context()
    items = store.list_assessments(org_id=ctx.org_id)
    return api_ok({"assessments": items, "total": len(items)})


@api_v1_bp.route('/aiact/assessments/<assessment_id>', methods=['GET'])
@require_org()
def aiact_get_assessment(assessment_id):
    ctx = get_org_context()
    a = store.get_assessment(assessment_id, org_id=ctx.org_id)
    if not a:
        return api_error("Assessment not found", 404)
    a["disclaimer"] = DISCLAIMER
    return api_ok(a)


@api_v1_bp.route('/aiact/assessments/<assessment_id>', methods=['PATCH'])
@require_org()
def aiact_update_assessment(assessment_id):
    ctx = get_org_context()
    existing = store.get_assessment(assessment_id, org_id=ctx.org_id)
    if not existing:
        return api_error("Assessment not found", 404)
    data = request.get_json() or {}
    kwargs = {}
    try:
        if "system_name" in data:
            kwargs["system_name"] = V.clean_system_name(data.get("system_name"))
        if "answers" in data:
            # Merge onto existing answers so partial saves accumulate.
            merged = dict(existing.get("answers") or {})
            merged.update(V.validate_answers(data.get("answers") or {}))
            kwargs["answers"] = merged
    except V.ValidationError as e:
        return api_error(str(e), 400)
    updated = store.update_assessment(assessment_id, org_id=ctx.org_id, **kwargs)
    return api_ok(updated)


# ── Scoring (the engine) ─────────────────────────────────────────────────────

@api_v1_bp.route('/aiact/assessments/<assessment_id>/score', methods=['POST'])
@require_org()
def aiact_score_assessment(assessment_id):
    ctx = get_org_context()
    existing = store.get_assessment(assessment_id, org_id=ctx.org_id)
    if not existing:
        return api_error("Assessment not found", 404)

    data = request.get_json() or {}
    # Allow submitting final answers with the score call.
    answers = dict(existing.get("answers") or {})
    if data.get("answers"):
        try:
            answers.update(V.validate_answers(data.get("answers")))
        except V.ValidationError as e:
            return api_error(str(e), 400)
    if not answers:
        return api_error("No answers to score — answer the questions first", 400)

    # Rate-limit the scoring path (per IP and per org).
    if not _rate_ok(_ip_buckets, _client_ip(), SCORE_IP_LIMIT, SCORE_IP_WINDOW_S):
        return api_error("Too many scoring requests from this network. Please try again later.", 429)
    if not _rate_ok(_org_buckets, ctx.org_id, SCORE_ORG_LIMIT, SCORE_ORG_WINDOW_S):
        return api_error("You've reached the scoring limit for now. Please try again later.", 429)

    use_ai = None
    if data.get("ai") in (True, "1", "true", "on"):
        use_ai = True
    elif data.get("ai") in (False, "0", "false", "off"):
        use_ai = False

    from services.aiact.engine import score_assessment
    result = score_assessment(system_name=existing.get("system_name") or "the AI system",
                              answers=answers, use_ai=use_ai)
    result["disclaimer"] = DISCLAIMER

    updated = store.update_assessment(
        assessment_id, org_id=ctx.org_id, answers=answers, result=result, status="scored",
    )

    # Audit the decision when the AI narrative path was used (EU AI Act Art. 13).
    if result.get("ai_generated"):
        try:
            from services.compliance.decision_logger import log_decision
            log_decision(
                org_id=ctx.org_id, decision_type="ai_act_readiness",
                candidate_id=assessment_id, opportunity_id=None,
                inputs={"channel": "aiact", "risk": result.get("risk_classification")},
                model_used=result.get("model_used"),
                score=round(result.get("readiness_score", 0) / 100.0, 2),
                explanation=result.get("summary"),
            )
        except Exception:
            pass

    return api_ok(updated)


# ── GDPR ─────────────────────────────────────────────────────────────────────

@api_v1_bp.route('/aiact/assessments/<assessment_id>/export', methods=['GET'])
@require_org()
def aiact_export_assessment(assessment_id):
    ctx = get_org_context()
    data = store.export_assessment(assessment_id, org_id=ctx.org_id)
    if not data:
        return api_error("Assessment not found", 404)
    return api_ok(data)


@api_v1_bp.route('/aiact/assessments/<assessment_id>', methods=['DELETE'])
@require_org()
def aiact_delete_assessment(assessment_id):
    ctx = get_org_context()
    ok = store.delete_assessment(assessment_id, org_id=ctx.org_id)
    if not ok:
        return api_error("Assessment not found", 404)
    return api_ok({"deleted": True, "assessment_id": assessment_id})
