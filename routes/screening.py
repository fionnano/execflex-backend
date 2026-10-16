"""
Screening routes: enqueue candidate screening calls and poll for results.

Rate limited: max 10 screening calls per hour per authenticated user.
"""
import time
import threading
import uuid
from flask import request, jsonify
from routes import screening_bp
from services.screening_service import create_screening_job
from utils.auth_helpers import require_auth
from utils.org_scope import is_service_identity, is_uuid, resolve_caller_org_id, resolve_job_org_id

# ── Per-user rate limiting for screening calls ────────────────────────────────
# Tracks {user_id: [timestamp, ...]} — sliding window, 10 per hour.
_screening_rate: dict = {}
_screening_rate_lock = threading.Lock()
_SCREENING_RATE_LIMIT = 10
_SCREENING_RATE_WINDOW = 3600  # 1 hour in seconds


def _check_screening_rate(user_id: str) -> bool:
    """Return True if the user is within the rate limit, False if exceeded."""
    now = time.time()
    cutoff = now - _SCREENING_RATE_WINDOW
    with _screening_rate_lock:
        timestamps = _screening_rate.get(user_id, [])
        # Prune old entries
        timestamps = [t for t in timestamps if t > cutoff]
        if len(timestamps) >= _SCREENING_RATE_LIMIT:
            _screening_rate[user_id] = timestamps
            return False
        timestamps.append(now)
        _screening_rate[user_id] = timestamps
        return True


@screening_bp.route("", methods=["POST"])
@screening_bp.route("/screen_candidate", methods=["POST"])
@require_auth
def screen_candidate():
    """
    POST /screen_candidate

    Body (JSON):
        candidate_phone       str  — E.164 phone number (e.g. +353861234567)
        candidate_name        str  — Full name
        role_title            str  — Job title being screened for
        company_name          str  — Hiring company name
        questions             list — [{question, competency, weight}, ...]
        callback_url          str  — Webhook URL for results (optional)
        source_candidate_id   str  — ID from calling system e.g. ainm.ai (optional)
        candidate_email       str  — Candidate email on record (optional; the only
                                     address Art 86 feedback is ever sent to)

    Returns:
        201 { job_id, thread_id, interaction_id, status }

    Rate limit: 10 calls per hour per authenticated user. Returns 429 if exceeded.
    """
    user_id = request.environ.get("authenticated_user_id", "unknown")
    is_service = user_id.startswith("service:") if user_id else False

    # Skip rate limit and quota for service-to-service calls (e.g. Ainm backend)
    if not is_service:
        if not _check_screening_rate(user_id):
            return jsonify({"error": "Rate limit exceeded: max 10 screening calls per hour"}), 429

        # Tier quota check
        from services.billing_service import check_quota
        allowed, quota_msg = check_quota(user_id, "screenings_done")
        if not allowed:
            return jsonify({"error": quota_msg, "error_code": "upgrade_required", "upgrade_url": "/pricing"}), 403

    data = request.get_json(force=True) or {}

    required = ("candidate_phone", "candidate_name", "role_title", "company_name", "questions")
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    questions = data["questions"]
    if not isinstance(questions, list) or not questions:
        return jsonify({"error": "questions must be a non-empty array"}), 400

    # Results webhooks carry the transcript and scores, so they only go to
    # allow-listed hosts and only for service integrations that can verify
    # the HMAC signature (GDPR-E-005).
    callback_url = data.get("callback_url") or None
    if callback_url:
        if not is_service:
            return jsonify({"error": "callback_url is only available to service integrations"}), 400
        from services.callback_policy import validate_callback_url
        reason = validate_callback_url(callback_url)
        if reason:
            return jsonify({"error": reason}), 400

    # Refuse to dial anyone on the do-not-contact list (GDPR-E-004).
    from services.compliance.suppression import is_suppressed
    if is_suppressed(email=data.get("candidate_email"), phone=data["candidate_phone"]):
        return jsonify({"error": "Candidate has opted out of contact", "code": "suppressed"}), 409

    # Stamp the caller's organisation on the job so later reads (status,
    # retry-extraction, bias audit) can be scoped to it. Service callers
    # (ainm's backend) have no ExecFlex organisation.
    organization_id = None if is_service else resolve_caller_org_id(user_id)

    try:
        result = create_screening_job(
            candidate_phone=data["candidate_phone"],
            candidate_name=data["candidate_name"],
            role_title=data["role_title"],
            company_name=data["company_name"],
            questions=questions,
            callback_url=callback_url,
            source_candidate_id=data.get("source_candidate_id"),
            purpose=data.get("purpose"),
            user_id=user_id if user_id not in ("unknown", None) and not user_id.startswith("service:") else None,
            role_id=data.get("role_id"),
            organization_id=organization_id,
            sign_callback=is_service,
            candidate_email=data.get("candidate_email"),
        )
        return jsonify(result), 201
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


def _load_job_for_caller(job_id: str):
    """
    Load an outbound_call_jobs row only if the authenticated caller may see it.

    Returns (job, None) on success, else (None, (response, status)).

    The Supabase service-role key bypasses RLS, so ownership is enforced here
    (SEC-E-004): a JWT user must have created the job or belong to the
    organisation that owns it (utils.org_scope.resolve_job_org_id). The
    service identity (ainm's backend, which creates these jobs for its own
    tenants) may read any job. Unknown and foreign jobs both answer 404 so
    the route does not reveal which ids exist.
    """
    from config.clients import supabase_client
    if not supabase_client:
        return None, (jsonify({"error": "Database not available"}), 503)
    if not is_uuid(job_id):
        return None, (jsonify({"error": "Job not found"}), 404)

    job_resp = (
        supabase_client.table("outbound_call_jobs")
        .select("id, user_id, interaction_id, artifacts")
        .eq("id", job_id)
        .limit(1)
        .execute()
    )
    if not job_resp.data:
        return None, (jsonify({"error": "Job not found"}), 404)
    job = job_resp.data[0]

    caller = request.environ.get("authenticated_user_id")
    if is_service_identity(caller):
        return job, None
    if job.get("user_id") and job.get("user_id") == caller:
        return job, None
    caller_org = resolve_caller_org_id(caller)
    job_org = resolve_job_org_id(job)
    if caller_org and job_org and caller_org == job_org:
        return job, None
    return None, (jsonify({"error": "Job not found"}), 404)


@screening_bp.route("/<job_id>/retry-extraction", methods=["POST"])
@require_auth
def retry_extraction(job_id: str):
    """
    POST /screening/<job_id>/retry-extraction

    Manually retry extraction for a completed call. Use when the automatic
    post-call extraction failed due to connection errors. The caller must
    own the job (see _load_job_for_caller).
    """
    try:
        job, denied = _load_job_for_caller(job_id)
        if denied:
            return denied

        interaction_id = job.get("interaction_id")
        if not interaction_id:
            return jsonify({"error": "No interaction found for this job"}), 404

        call_type = (job.get("artifacts") or {}).get("call_type", "screening")
        print(f"[RetryExtraction] job={job_id}, interaction={interaction_id}, call_type={call_type}", flush=True)

        from services.call_extraction_service import extract_candidate_profile_async, extract_employer_brief_async
        if call_type == "employer_brief":
            extract_employer_brief_async(interaction_id, job_id)
        else:
            extract_candidate_profile_async(interaction_id, job_id)

        return jsonify({"status": "extraction_queued", "interaction_id": interaction_id}), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@screening_bp.route("/<job_id>/status", methods=["GET"])
@require_auth
def screening_status(job_id: str):
    """
    GET /screening/<job_id>/status

    Returns the current status of a screening call and, once complete,
    the full scores and transcript.

    Response:
        {
          job_id, status, interaction_id,
          transcript?,   — only when completed
          scores?,       — only when completed
          recommendation? — only when completed
        }

    Status values: queued | ringing | in_progress | completed | failed | no_answer

    The caller must own the job (see _load_job_for_caller); other
    organisations' jobs answer 404.
    """
    try:
        _, denied = _load_job_for_caller(job_id)
        if denied:
            return denied

        from services.screening_service import get_screening_status
        response = get_screening_status(job_id)
        if response is None:
            return jsonify({"error": "Job not found"}), 404

        return jsonify(response), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ── Bias audit and policy endpoints (EU AI Act compliance) ───────────────────

@screening_bp.route("/bias-audit/<role_id>", methods=["GET"])
@require_auth
def bias_audit(role_id: str):
    """
    GET /screening/bias-audit/<role_id>

    Returns bias audit summary and records for one of the caller's roles.

    role_id must be the opportunity UUID. Records are filtered to the
    caller's organisation with parameterised .eq() filters; the path value
    is never interpolated into a PostgREST filter string (SEC-E-003).
    """
    if not is_uuid(role_id):
        return jsonify({"error": "role_id must be a UUID"}), 400
    role_id = str(uuid.UUID(role_id))

    org_id = resolve_caller_org_id(request.environ.get("authenticated_user_id"))
    if not org_id:
        return jsonify({"error": "Organization context required"}), 403

    try:
        from config.clients import supabase_client
        if not supabase_client:
            return jsonify({"error": "Database not available"}), 503

        try:
            limit = max(1, min(int(request.args.get("limit", 50)), 200))
            offset = max(0, int(request.args.get("offset", 0)))
        except (TypeError, ValueError):
            return jsonify({"error": "limit and offset must be integers"}), 400

        resp = (
            supabase_client.table("screening_bias_audit")
            .select("*", count="exact")
            .eq("organization_id", org_id)
            .eq("role_id", role_id)
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
            .execute()
        )
        records = resp.data or []
        total = resp.count if resp.count is not None else len(records)

        if not records:
            return jsonify({"role_id": role_id, "total_screenings": 0, "summary": {}, "records": []}), 200

        scored = [r for r in records if r.get("overall_score") is not None]
        avg_score = sum(r["overall_score"] for r in scored) / max(len(scored), 1)
        all_flags = []
        for r in records:
            all_flags.extend(r.get("bias_flags") or [])
        question_consistency = sum(
            1 for r in records
            if r.get("questions_asked") == r.get("questions_expected") and r.get("question_order_preserved")
        ) / max(total, 1)
        disclosure_count = sum(1 for r in records if r.get("ai_disclosure_given"))
        consent_count = sum(1 for r in records if r.get("candidate_consented"))

        if question_consistency >= 0.95 and len(all_flags) == 0:
            risk_rating = "Low"
        elif question_consistency >= 0.8 and len(all_flags) <= 2:
            risk_rating = "Medium"
        else:
            risk_rating = "High"

        return jsonify({
            "role_id": role_id,
            "total_screenings": total,
            "summary": {
                "average_score": round(avg_score, 2),
                "question_consistency_rate": round(question_consistency, 2),
                "ai_disclosure_rate": round(disclosure_count / max(total, 1), 2),
                "consent_rate": round(consent_count / max(total, 1), 2),
                "total_bias_flags": len(all_flags),
                "unique_bias_flags": list(set(all_flags)),
                "bias_risk_rating": risk_rating,
            },
            "compliance_statement": (
                "This screening process is designed in accordance with EU AI Act "
                "requirements for high-risk AI in recruitment (Annex III) and EU "
                "Employment Equality legislation."
            ),
            "records": records,
        }), 200
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@screening_bp.route("/bias-policy", methods=["GET"])
def bias_policy():
    """
    GET /screening/bias-policy

    Public transparency endpoint — EU AI Act bias prevention policy.
    No authentication required.
    """
    return jsonify({
        "policy_version": "1.0",
        "effective_date": "2026-03-30",
        "framework": "EU AI Act (Regulation 2024/1689)",
        "classification": "High-risk AI system — employment and recruitment (Article 6, Annex III)",
        "ai_system": {"name": "AI Dan", "provider": "Ainm Search", "purpose": "Initial candidate screening via structured voice interview"},
        "controls": {
            "question_consistency": "All candidates for the same role receive identical questions in identical order.",
            "language_neutrality": "Plain, neutral, competency-based language only. Gender-coded terms banned.",
            "protected_characteristics": "Absolutely prohibited from asking about age, gender, race, ethnicity, religion, disability, sexual orientation, marital status, pregnancy, nationality, or salary history (EU Directive 2023/970 Art. 5).",
            "accent_accommodation": "Extended silence tolerance (4s+). AI requests repetition rather than guessing. No accent/fluency penalty.",
            "neurodivergent_accommodation": "Non-linear answers accepted. Extended thinking time (20s). Filler words not penalised.",
            "scoring_objectivity": "Fixed competency rubric (1-5). Only relevance, evidence, outcome. Style/confidence/accent excluded.",
            "ai_disclosure": "Candidates told they're speaking with AI. Can decline and speak to a human.",
            "human_oversight": "All AI scores reviewed by human recruiter before any hiring decision.",
            "data_processing": "Recorded and processed under GDPR. Candidates can request deletion.",
            "bias_self_check": "Scoring AI self-checks: would identical scores be given with different name/accent/style?",
        },
        "scoring_rubric": {"1": "No relevant evidence", "2": "Limited evidence", "3": "Meets expectations", "4": "Strong evidence", "5": "Exceptional evidence"},
        "candidate_rights": {
            "right_to_know": "Informed they are assessed by AI (EU AI Act Article 50)",
            "right_to_decline": "Can opt out and speak to a human",
            "right_to_explanation": "Can request feedback (EU AI Act Article 86)",
            "right_to_deletion": "Can request data deletion (GDPR Article 17)",
        },
        "audit_trail": "Every screening generates a bias audit record: GET /screening/bias-audit/{role_id}",
        "contact": "compliance@ainm.ai",
    }), 200


def _job_screening_context(job: dict) -> dict:
    return (job.get("artifacts") or {}).get("screening_context") or {}


def _latest_job_for_user(supabase_client, user_id: str):
    resp = (
        supabase_client.table("outbound_call_jobs")
        .select("id, interaction_id, artifacts, user_id")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


def _find_feedback_job(supabase_client, email: str, phone: str):
    """Most recent screening job for this phone (preferred) or email."""
    if phone:
        normalized_phone = phone if phone.startswith("+") else "+" + phone.replace(" ", "").replace("-", "")
        job_resp = (
            supabase_client.table("outbound_call_jobs")
            .select("id, interaction_id, artifacts, user_id")
            .eq("phone_e164", normalized_phone)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if job_resp.data and job_resp.data[0].get("interaction_id"):
            return job_resp.data[0]

    if email:
        ci_resp = (
            supabase_client.table("channel_identities")
            .select("user_id")
            .eq("channel", "email")
            .eq("value", email)
            .limit(1)
            .execute()
        )
        if ci_resp.data and ci_resp.data[0].get("user_id"):
            job = _latest_job_for_user(supabase_client, ci_resp.data[0]["user_id"])
            if job and job.get("interaction_id"):
                return job
    return None


def _email_on_record(supabase_client, job: dict):
    """The candidate email we already hold for this screening job, or None.

    Sources, in order: the email captured when the job was created
    (screening_context.candidate_email), then the email channel identity of
    the job's platform user. Never anything the feedback requester supplied.
    """
    ctx_email = (_job_screening_context(job).get("candidate_email") or "").strip().lower()
    if ctx_email:
        return ctx_email
    user_id = job.get("user_id")
    if user_id:
        ci_resp = (
            supabase_client.table("channel_identities")
            .select("value")
            .eq("channel", "email")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if ci_resp.data:
            value = (ci_resp.data[0].get("value") or "").strip().lower()
            if value:
                return value
    return None


# One Art 86 request per phone/e-mail/token per hour: the lookup keys are
# not secrets, so without this a caller could flood a candidate's inbox.
_FEEDBACK_THROTTLE_SECONDS = 3600
_feedback_requests_seen: dict = {}


def _feedback_throttled(key: str) -> bool:
    now = time.time()
    for k, ts in list(_feedback_requests_seen.items()):
        if now - ts > _FEEDBACK_THROTTLE_SECONDS:
            _feedback_requests_seen.pop(k, None)
    if key in _feedback_requests_seen:
        return True
    _feedback_requests_seen[key] = now
    return False


def _feedback_accepted():
    """The one response every outcome gets (SEC-E-006).

    Whether a screening exists, has scores or has an address on file is
    answered only in the mailbox on record, never to the caller, so the
    endpoint cannot be used to enumerate screened phone numbers.
    """
    return jsonify({
        "status": "accepted",
        "message": (
            "Thanks. If we hold a screening record for these details, the "
            "feedback will be sent to the email address we have on file for it. "
            "If you do not receive it, contact compliance@ainm.ai."
        ),
    }), 202


def _find_feedback_job_by_token(supabase_client, candidate_token: str):
    """The screening job whose interaction carries this portal token.

    The token is issued to the candidate only (portal link in the results
    e-mail), so presenting it proves ownership of the screening.
    """
    ix_resp = (
        supabase_client.table("interactions")
        .select("id")
        .eq("candidate_token", candidate_token)
        .limit(1)
        .execute()
    )
    if not ix_resp.data:
        return None
    job_resp = (
        supabase_client.table("outbound_call_jobs")
        .select("id, interaction_id, artifacts, user_id")
        .eq("interaction_id", ix_resp.data[0]["id"])
        .limit(1)
        .execute()
    )
    return job_resp.data[0] if job_resp.data else None


@screening_bp.route("/feedback-request", methods=["POST"])
def feedback_request():
    """
    POST /screening/feedback-request

    Candidate self-service: request an explanation of their screening outcome.
    EU AI Act Article 86 compliance.

    Body (JSON):
        candidate_token str — portal token from the results e-mail (preferred;
                              proves ownership of the screening)
        email           str — candidate's email address
        phone           str — candidate's phone (alternative lookup)

    No login — candidates may not have an account. Because of that
    (SEC-E-006):
      * email/phone in the body are only LOOKUP keys; the feedback goes solely
        to the email address already on record for that screening, never to
        a caller-supplied address;
      * every outcome returns the same 202, so the endpoint reveals nothing
        about whether a phone number was screened;
      * one request per key per hour.
    """
    data = request.get_json(force=True, silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    phone = (data.get("phone") or "").strip()
    candidate_token = (data.get("candidate_token") or "").strip()

    if not email and not phone and not candidate_token:
        return jsonify({"error": "candidate_token, email or phone is required"}), 400

    throttle_key = candidate_token or phone or email
    if _feedback_throttled(f"feedback:{throttle_key}"):
        return _feedback_accepted()

    try:
        from config.clients import supabase_client
        from datetime import datetime, timezone
        if not supabase_client:
            return jsonify({"error": "Service unavailable"}), 503

        now_iso = datetime.now(timezone.utc).isoformat()

        if candidate_token:
            job = _find_feedback_job_by_token(supabase_client, candidate_token)
        else:
            job = _find_feedback_job(supabase_client, email, phone)

        if not job:
            # Log the request even if no screening found
            supabase_client.table("screening_feedback_requests").insert({
                "candidate_email": email or phone or "token",
                "candidate_phone": phone,
                "status": "no_data",
                "error_message": "No screening found for this candidate",
                "requested_at": now_iso,
            }).execute()
            return _feedback_accepted()

        job_id = job["id"]
        interaction_id = job.get("interaction_id")
        ctx = _job_screening_context(job)
        candidate_name = ctx.get("candidate_name", "Candidate")
        role_title = ctx.get("role_title", "the role")
        company_name = ctx.get("company_name", "the company")
        scores = []
        overall_score = None
        recommendation = None

        # Get scores from interaction
        ix_resp = (
            supabase_client.table("interactions")
            .select("screening_scores, screening_recommendation")
            .eq("id", interaction_id)
            .limit(1)
            .execute()
        )
        if ix_resp.data:
            ix = ix_resp.data[0] or {}
            scores = ix.get("screening_scores") or []
            recommendation = ix.get("screening_recommendation")
            # Calculate overall from scores
            numeric = [s["score"] for s in scores if isinstance(s.get("score"), (int, float))]
            overall_score = round(sum(numeric) / max(len(numeric), 1), 1) if numeric else None

        if not scores:
            supabase_client.table("screening_feedback_requests").insert({
                "candidate_email": email or phone,
                "candidate_phone": phone,
                "interaction_id": interaction_id,
                "job_id": job_id,
                "role_title": role_title,
                "status": "no_data",
                "error_message": "Screening found but scores not yet available",
                "requested_at": now_iso,
            }).execute()
            return _feedback_accepted()

        # Send feedback ONLY to the email on record for this screening.
        feedback_email = _email_on_record(supabase_client, job)
        if not feedback_email:
            supabase_client.table("screening_feedback_requests").insert({
                "candidate_email": email or phone or "token",
                "candidate_phone": phone,
                "interaction_id": interaction_id,
                "job_id": job_id,
                "role_title": role_title,
                "status": "failed",
                "error_message": "No email address on record for this screening",
                "requested_at": now_iso,
            }).execute()
            return _feedback_accepted()

        from modules.email_sender import send_screening_feedback_email
        sent = send_screening_feedback_email(
            candidate_email=feedback_email,
            candidate_name=candidate_name,
            role_title=role_title,
            company_name=company_name,
            overall_score=overall_score or 0,
            scores=scores,
            recommendation=recommendation or "pending",
        )

        supabase_client.table("screening_feedback_requests").insert({
            "candidate_email": feedback_email,
            "candidate_phone": phone,
            "interaction_id": interaction_id,
            "job_id": job_id,
            "role_title": role_title,
            "status": "sent" if sent else "failed",
            "sent_at": now_iso if sent else None,
            "error_message": None if sent else "Email delivery failed",
            "requested_at": now_iso,
        }).execute()

        if not sent:
            print(f"[Feedback] delivery failed for job={job_id}", flush=True)
        # Don't echo the address or the outcome: the requester may not be
        # the candidate.
        return _feedback_accepted()

    except Exception:
        import traceback
        traceback.print_exc()
        return jsonify({"error": "Request could not be processed"}), 500


@screening_bp.route("/candidate-status", methods=["GET"])
def candidate_status():
    """
    GET /screening/candidate-status?token=<uuid>

    Candidate portal — returns screening summary for the given token.
    No auth required (token acts as auth).
    Returns 404 if not found, 410 if older than 90 days.
    """
    token = request.args.get("token", "").strip()
    if not token:
        return jsonify({"error": "token query parameter is required"}), 400

    try:
        from config.clients import supabase_client
        from datetime import datetime, timezone, timedelta
        if not supabase_client:
            return jsonify({"error": "Service unavailable"}), 503

        ix_resp = (
            supabase_client.table("interactions")
            .select("id, started_at, ended_at, screening_recommendation, created_at")
            .eq("candidate_token", token)
            .limit(1)
            .execute()
        )
        if not ix_resp.data:
            return jsonify({"error": "Screening not found for this token"}), 404

        ix = ix_resp.data[0]
        interaction_id = ix["id"]

        # Check 90-day expiry
        created_str = ix.get("created_at") or ix.get("started_at")
        if created_str:
            try:
                created_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                if datetime.now(timezone.utc) - created_dt > timedelta(days=90):
                    return jsonify({"error": "This screening link has expired (older than 90 days)"}), 410
            except (ValueError, TypeError):
                pass

        # Get job context
        job_resp = (
            supabase_client.table("outbound_call_jobs")
            .select("artifacts")
            .eq("interaction_id", interaction_id)
            .limit(1)
            .execute()
        )
        candidate_name = "Candidate"
        role_title = "the role"
        company_name = "the company"
        duration_seconds = 0
        human_review = None

        if job_resp.data:
            artifacts = (job_resp.data[0] or {}).get("artifacts") or {}
            ctx = artifacts.get("screening_context") or {}
            candidate_name = ctx.get("candidate_name", candidate_name)
            role_title = ctx.get("role_title", role_title)
            company_name = ctx.get("company_name", company_name)
            duration_seconds = int(artifacts.get("call_duration") or 0)
            human_review = artifacts.get("human_review")

        # Map recommendation to candidate-friendly status. An AI "reject" is
        # shown as under review until a recruiter confirms it (GDPR Art. 22).
        from services.compliance.human_review import candidate_facing_outcome
        rec = candidate_facing_outcome(ix.get("screening_recommendation"), human_review)
        status_map = {
            "strong_proceed": "progressed",
            "proceed": "progressed",
            "hold": "under_review",
            "pending_human_review": "under_review",
            "reject": "not_progressed",
            "incomplete": "under_review",
        }

        return jsonify({
            "candidate_name": candidate_name,
            "role_title": role_title,
            "company_name": company_name,
            "screening_date": ix.get("started_at") or ix.get("created_at"),
            "duration_seconds": duration_seconds,
            "status": status_map.get(rec, "under_review"),
            "token": token,
        }), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


_HUMAN_DECISIONS = ("strong_proceed", "proceed", "hold", "reject")


@screening_bp.route("/<job_id>/human-review", methods=["POST"])
@require_auth
def record_human_review(job_id: str):
    """
    POST /screening/<job_id>/human-review

    A recruiter records the decision on an AI screening. Until this is called,
    an AI "reject" is shown to the candidate as "under review" and the Art 86
    feedback email does not say "not proceeding" (GDPR Art. 22 — no solely
    automated adverse decision).

    Body (JSON):
        decision  str — strong_proceed | proceed | hold | reject
        reason    str — required for reject (candidate's right to explanation)
        reviewer  str — recruiter identity; required for service callers (ainm),
                        whose own recruiter made the decision

    Allowed callers: the user who created the screening, or the ainm service.
    """
    user_id = request.environ.get("authenticated_user_id") or ""
    is_service = user_id.startswith("service:")
    data = request.get_json(force=True) or {}

    decision = (data.get("decision") or "").strip().lower()
    if decision not in _HUMAN_DECISIONS:
        return jsonify({"error": f"decision must be one of {', '.join(_HUMAN_DECISIONS)}"}), 400
    reason = (data.get("reason") or "").strip()
    reviewer_id = (data.get("reviewer") or "").strip() if is_service else user_id
    if not reviewer_id:
        return jsonify({"error": "reviewer is required for service callers"}), 400

    try:
        from config.clients import supabase_client
        from datetime import datetime, timezone
        from types import SimpleNamespace
        from services.compliance.human_review import require_human_review_for_reject
        if not supabase_client:
            return jsonify({"error": "Database not available"}), 503

        job_resp = (
            supabase_client.table("outbound_call_jobs")
            .select("id, user_id, interaction_id, artifacts")
            .eq("id", job_id)
            .limit(1)
            .execute()
        )
        if not job_resp.data:
            return jsonify({"error": "Screening job not found"}), 404
        job = job_resp.data[0]
        if not is_service and job.get("user_id") != user_id:
            return jsonify({"error": "Screening job not found"}), 404

        if decision == "reject":
            check = require_human_review_for_reject(
                SimpleNamespace(user_id=reviewer_id), job_id, reason)
            if not check["allowed"]:
                return jsonify({"error": check["message"]}), 400

        ai_recommendation = None
        if job.get("interaction_id"):
            ix_resp = (
                supabase_client.table("interactions")
                .select("screening_recommendation")
                .eq("id", job["interaction_id"])
                .limit(1)
                .execute()
            )
            if ix_resp.data:
                ai_recommendation = (ix_resp.data[0] or {}).get("screening_recommendation")

        human_review = {
            "decision": decision,
            "reason": reason or None,
            "reviewer_id": reviewer_id,
            "reviewed_via": "service" if is_service else "user",
            "ai_recommendation": ai_recommendation,
            "human_override": bool(ai_recommendation) and ai_recommendation != decision,
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
        }
        artifacts = dict(job.get("artifacts") or {})
        artifacts["human_review"] = human_review
        supabase_client.table("outbound_call_jobs").update(
            {"artifacts": artifacts}
        ).eq("id", job_id).execute()

        return jsonify({"job_id": job_id, "human_review": human_review}), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
