"""
Screening routes: enqueue candidate screening calls and poll for results.
"""
from flask import request, jsonify
from routes import screening_bp
from services.screening_service import create_screening_job


@screening_bp.route("", methods=["POST"])
@screening_bp.route("/screen_candidate", methods=["POST"])
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
    """
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
        )
        return jsonify(result), 201
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@screening_bp.route("/<job_id>/status", methods=["GET"])
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
                .select("transcript_text, screening_scores, screening_recommendation")
                .eq("id", interaction_id)
                .limit(1)
                .execute()
            )
            if interaction_resp.data:
                ix = interaction_resp.data[0] or {}
                response["transcript"] = ix.get("transcript_text")
                response["scores"] = ix.get("screening_scores")
                response["recommendation"] = ix.get("screening_recommendation")

        return jsonify(response), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
