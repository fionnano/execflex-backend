"""
Screening routes: enqueue candidate screening calls and poll for results.

Rate limited: max 10 screening calls per hour per authenticated user.
"""
import time
import threading
from flask import request, jsonify
from routes import screening_bp
from services.screening_service import create_screening_job
from utils.auth_helpers import require_auth

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

    try:
        result = create_screening_job(
            candidate_phone=data["candidate_phone"],
            candidate_name=data["candidate_name"],
            role_title=data["role_title"],
            company_name=data["company_name"],
            questions=questions,
            callback_url=data.get("callback_url"),
            source_candidate_id=data.get("source_candidate_id"),
            purpose=data.get("purpose"),
            user_id=user_id if user_id not in ("unknown", None) and not user_id.startswith("service:") else None,
            role_id=data.get("role_id"),
        )
        return jsonify(result), 201
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@screening_bp.route("/<job_id>/retry-extraction", methods=["POST"])
@require_auth
def retry_extraction(job_id: str):
    """
    POST /screening/<job_id>/retry-extraction

    Manually retry extraction for a completed call. Use when the automatic
    post-call extraction failed due to connection errors.
    """
    try:
        from config.clients import supabase_client
        if not supabase_client:
            return jsonify({"error": "Database not available"}), 503

        job_resp = (
            supabase_client.table("outbound_call_jobs")
            .select("id, interaction_id, artifacts")
            .eq("id", job_id)
            .limit(1)
            .execute()
        )
        if not job_resp.data:
            return jsonify({"error": "Job not found"}), 404

        job = job_resp.data[0]
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
    """
    try:
        from config.clients import supabase_client
        if not supabase_client:
            return jsonify({"error": "Database not available"}), 503

        job_resp = (
            supabase_client.table("outbound_call_jobs")
            .select("*")
            .eq("id", job_id)
            .limit(1)
            .execute()
        )
        if not job_resp.data:
            return jsonify({"error": "Job not found"}), 404

        job = job_resp.data[0]
        artifacts = job.get("artifacts", {}) or {}
        call_status_raw = artifacts.get("call_status")
        interaction_id = job.get("interaction_id")

        # Map to screening status
        job_status_map = {
            "queued": "queued",
            "running": "in_progress",
            "succeeded": "completed",
            "failed": "failed",
        }
        twilio_status_map = {
            "queued": "queued",
            "ringing": "ringing",
            "in-progress": "in_progress",
            "completed": "completed",
            "failed": "failed",
            "busy": "failed",
            "no-answer": "no_answer",
            "canceled": "failed",
        }
        if call_status_raw:
            status = twilio_status_map.get(call_status_raw, job_status_map.get(job["status"], "queued"))
        else:
            status = job_status_map.get(job["status"], "queued")

        response: dict = {
            "job_id": job_id,
            "status": status,
            "interaction_id": interaction_id,
        }

        if status == "completed" and interaction_id:
            interaction_resp = (
                supabase_client.table("interactions")
                .select("transcript_text, screening_scores, screening_recommendation, artifacts")
                .eq("id", interaction_id)
                .limit(1)
                .execute()
            )
            if interaction_resp.data:
                ix = interaction_resp.data[0] or {}
                response["transcript"] = ix.get("transcript_text")
                response["scores"] = ix.get("screening_scores")
                response["recommendation"] = ix.get("screening_recommendation")
                # Include extracted profile/brief data from post-call analysis
                ix_artifacts = ix.get("artifacts") or {}
                if ix_artifacts.get("candidate_extraction"):
                    response["candidate_profile"] = ix_artifacts["candidate_extraction"]
                if ix_artifacts.get("employer_extraction"):
                    response["employer_brief"] = ix_artifacts["employer_extraction"]
                # Tell frontend whether extraction is complete or still processing
                has_extraction = bool(
                    ix_artifacts.get("candidate_extraction")
                    or ix_artifacts.get("employer_extraction")
                )
                has_error = bool(
                    (ix_artifacts.get("candidate_extraction") or {}).get("error")
                    or (ix_artifacts.get("employer_extraction") or {}).get("error")
                )
                if has_extraction and not has_error:
                    response["extraction_status"] = "complete"
                elif has_error:
                    response["extraction_status"] = "failed"
                else:
                    response["extraction_status"] = "processing"

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

    Returns bias audit summary and records for a given role.
    """
    try:
        from config.clients import supabase_client
        if not supabase_client:
            return jsonify({"error": "Database not available"}), 503

        limit = min(int(request.args.get("limit", 50)), 200)
        offset = int(request.args.get("offset", 0))

        resp = (
            supabase_client.table("screening_bias_audit")
            .select("*", count="exact")
            .or_(f"role_id.eq.{role_id},role_title.eq.{role_id}")
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


@screening_bp.route("/feedback-request", methods=["POST"])
def feedback_request():
    """
    POST /screening/feedback-request

    Candidate self-service: request an explanation of their screening outcome.
    EU AI Act Article 86 compliance.

    Body (JSON):
        email   str — candidate's email address
        phone   str — candidate's phone (optional, alternative lookup)

    No auth required — candidates may not have an account.
    """
    data = request.get_json(force=True) or {}
    email = (data.get("email") or "").strip().lower()
    phone = (data.get("phone") or "").strip()

    if not email and not phone:
        return jsonify({"error": "email or phone is required"}), 400

    try:
        from config.clients import supabase_client
        from datetime import datetime, timezone
        if not supabase_client:
            return jsonify({"error": "Service unavailable"}), 503

        now_iso = datetime.now(timezone.utc).isoformat()

        # Look up the most recent screening for this candidate
        # Search by phone in outbound_call_jobs, or by email in channel_identities
        interaction_id = None
        job_id = None
        candidate_name = "Candidate"
        role_title = "the role"
        company_name = "the company"
        scores = []
        overall_score = None
        recommendation = None

        # Try phone lookup first (most screening calls are by phone)
        if phone:
            normalized_phone = phone if phone.startswith("+") else "+" + phone.replace(" ", "").replace("-", "")
            job_resp = (
                supabase_client.table("outbound_call_jobs")
                .select("id, interaction_id, artifacts")
                .eq("phone_e164", normalized_phone)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            if job_resp.data:
                job = job_resp.data[0]
                job_id = job["id"]
                interaction_id = job.get("interaction_id")
                ctx = (job.get("artifacts") or {}).get("screening_context") or {}
                candidate_name = ctx.get("candidate_name", candidate_name)
                role_title = ctx.get("role_title", role_title)
                company_name = ctx.get("company_name", company_name)

        # Fallback: try email lookup via channel_identities → user → jobs
        if not interaction_id and email:
            ci_resp = (
                supabase_client.table("channel_identities")
                .select("user_id")
                .eq("channel", "email")
                .eq("value", email)
                .limit(1)
                .execute()
            )
            if ci_resp.data:
                user_id = ci_resp.data[0].get("user_id")
                if user_id:
                    job_resp = (
                        supabase_client.table("outbound_call_jobs")
                        .select("id, interaction_id, artifacts")
                        .eq("user_id", user_id)
                        .order("created_at", desc=True)
                        .limit(1)
                        .execute()
                    )
                    if job_resp.data:
                        job = job_resp.data[0]
                        job_id = job["id"]
                        interaction_id = job.get("interaction_id")
                        ctx = (job.get("artifacts") or {}).get("screening_context") or {}
                        candidate_name = ctx.get("candidate_name", candidate_name)
                        role_title = ctx.get("role_title", role_title)
                        company_name = ctx.get("company_name", company_name)

        if not interaction_id:
            # Log the request even if no screening found
            supabase_client.table("screening_feedback_requests").insert({
                "candidate_email": email or phone,
                "candidate_phone": phone,
                "status": "no_data",
                "error_message": "No screening found for this candidate",
                "requested_at": now_iso,
            }).execute()
            return jsonify({
                "status": "no_screening_found",
                "message": "We couldn't find a screening record for this email/phone. Please contact compliance@ainm.ai for assistance.",
            }), 404

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
            return jsonify({
                "status": "scores_not_ready",
                "message": "Your screening has been found but scores are still being processed. Please try again in a few minutes.",
            }), 202

        # Send feedback email
        feedback_email = email
        if not feedback_email:
            supabase_client.table("screening_feedback_requests").insert({
                "candidate_email": phone,
                "candidate_phone": phone,
                "interaction_id": interaction_id,
                "job_id": job_id,
                "role_title": role_title,
                "status": "failed",
                "error_message": "No email address available to send feedback",
                "requested_at": now_iso,
            }).execute()
            return jsonify({
                "status": "email_required",
                "message": "Please provide your email address so we can send the feedback.",
            }), 400

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

        if sent:
            return jsonify({
                "status": "sent",
                "message": f"Screening feedback has been sent to {feedback_email}. Check your inbox.",
            }), 200
        else:
            return jsonify({
                "status": "failed",
                "message": "We found your screening but could not send the email. Please contact compliance@ainm.ai.",
            }), 500

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
