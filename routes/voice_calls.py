"""
Cara outbound voice call routes — onboarding welcome, reference check, exit interview.

Each endpoint creates an outbound_call_job that the existing Twilio dispatcher
picks up and dials. The call uses the same OpenAI Realtime pipeline as screening,
with call_type-specific prompts and post-call AI analysis.

POST /voice-call/onboarding     — welcome call to new employee
POST /voice-call/reference      — reference check call to referee
POST /voice-call/exit-interview — exit interview with departing employee
"""
from flask import Blueprint, request, jsonify
from services.voice_call_service import (
    create_onboarding_call_job,
    create_reference_call_job,
    create_exit_interview_call_job,
)

voice_calls_bp = Blueprint("voice_calls", __name__, url_prefix="/voice-call")


@voice_calls_bp.route("/onboarding", methods=["POST"])
def onboarding_welcome_call():
    """
    POST /voice-call/onboarding

    Body (JSON):
        employee_phone         str  — E.164 phone number
        employee_name          str  — Full name
        company_name           str  — Company name
        start_date             str  — ISO date string (e.g. "2026-03-16")
        manager_name           str  — Manager's full name
        office_location        str  — Office / work location (optional)
        first_day_instructions str  — Free text notes about the first day (optional)
        callback_url           str  — Webhook URL for results (optional)
        source_tracker_id      str  — OnboardingTracker ID from ainm.ai (optional)

    Returns:
        201 { job_id, interaction_id, status }
    """
    data = request.get_json(force=True) or {}
    required = ("employee_phone", "employee_name", "company_name", "start_date", "manager_name")
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    try:
        result = create_onboarding_call_job(
            employee_phone=data["employee_phone"],
            employee_name=data["employee_name"],
            company_name=data["company_name"],
            start_date=data["start_date"],
            manager_name=data["manager_name"],
            office_location=data.get("office_location", "the office"),
            first_day_instructions=data.get("first_day_instructions", ""),
            callback_url=data.get("callback_url"),
            source_tracker_id=data.get("source_tracker_id"),
        )
        return jsonify(result), 201
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@voice_calls_bp.route("/reference", methods=["POST"])
def reference_check_call():
    """
    POST /voice-call/reference

    Body (JSON):
        referee_phone      str  — E.164 phone number
        referee_name       str  — Referee's full name
        candidate_name     str  — Candidate's full name
        role_title         str  — Role the candidate applied for
        company_name       str  — Hiring company name
        relationship       str  — Referee's relationship to candidate (e.g. "Line Manager")
        questions          list — Optional custom questions (defaults to standard reference set)
        callback_url       str  — Webhook URL for results (optional)
        source_ref_id      str  — ReferenceCheck ID from ainm.ai (optional)

    Returns:
        201 { job_id, interaction_id, status }
    """
    data = request.get_json(force=True) or {}
    required = ("referee_phone", "referee_name", "candidate_name", "role_title", "company_name")
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    default_questions = [
        "How long did you work with the candidate and in what capacity?",
        "What was their role and main responsibilities?",
        "What would you say are their key strengths?",
        "Were there any areas where you felt they needed development?",
        "How did they handle pressure or difficult situations?",
        "Would you rehire them given the opportunity?",
        "Is there anything else you think we should know?",
    ]

    try:
        result = create_reference_call_job(
            referee_phone=data["referee_phone"],
            referee_name=data["referee_name"],
            candidate_name=data["candidate_name"],
            role_title=data["role_title"],
            company_name=data["company_name"],
            relationship=data.get("relationship", "colleague"),
            questions=data.get("questions") or default_questions,
            callback_url=data.get("callback_url"),
            source_ref_id=data.get("source_ref_id"),
        )
        return jsonify(result), 201
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@voice_calls_bp.route("/exit-interview", methods=["POST"])
def exit_interview_call():
    """
    POST /voice-call/exit-interview

    Body (JSON):
        employee_phone str  — E.164 phone number
        employee_name  str  — Employee's full name
        company_name   str  — Company name
        role_title     str  — Employee's role/job title
        tenure         str  — Human-readable tenure (e.g. "2 years and 3 months")
        manager_name   str  — Direct manager's name (optional)
        callback_url   str  — Webhook URL for results (optional)
        source_user_id str  — User ID from ainm.ai (optional)

    Returns:
        201 { job_id, interaction_id, status }
    """
    data = request.get_json(force=True) or {}
    required = ("employee_phone", "employee_name", "company_name", "role_title", "tenure")
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    try:
        result = create_exit_interview_call_job(
            employee_phone=data["employee_phone"],
            employee_name=data["employee_name"],
            company_name=data["company_name"],
            role_title=data["role_title"],
            tenure=data["tenure"],
            manager_name=data.get("manager_name", "their manager"),
            callback_url=data.get("callback_url"),
            source_user_id=data.get("source_user_id"),
        )
        return jsonify(result), 201
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
