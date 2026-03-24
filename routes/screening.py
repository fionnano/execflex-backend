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
