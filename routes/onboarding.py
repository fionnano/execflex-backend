"""
Onboarding service routes for outbound call management.
Handles onboarding calls triggered after user signup.
"""
import os
import requests
from flask import request, Response, jsonify
from routes import onboarding_bp
from utils.response_helpers import ok, bad
from utils.auth_helpers import require_admin, get_authenticated_user_id
from services.onboarding_service import initialize_user_onboarding, process_queued_jobs
from services.platform_config_service import (
    get_bool_config,
    set_bool_config,
    get_number_config,
    set_number_config,
    get_string_config,
    set_string_config,
)
from config.clients import twilio_client, supabase_client
from config.app_config import TWILIO_PHONE_NUMBER, SUPABASE_URL, SUPABASE_KEY


@onboarding_bp.route("/enqueue", methods=["POST"])
@require_admin
def enqueue_call():
    """
    Manually trigger onboarding for a user (admin-only).
    
    **ADMIN ONLY**: This endpoint requires authentication AND admin role.
    Used for manual/admin triggers, testing, or re-triggering onboarding.
    
    **Note:** Automatic onboarding is handled by database trigger on signup,
    so this endpoint is primarily for admin operations or edge cases.
    
    Headers:
        Authorization: Bearer <supabase_jwt_token>
    
    Body (JSON, required): { "user_id": "uuid" }
        - The user_id to initialize onboarding for (can be any user, not just the authenticated admin)
    """
    try:
        # Get authenticated admin user (for logging/audit)
        admin_user_id = request.environ.get('authenticated_user_id')
        
        data = request.get_json(silent=True) or {}
        target_user_id = data.get("user_id")
        
        if not target_user_id:
            return bad("user_id is required", 400)
        
        print(f"🔐 Admin {admin_user_id} triggering onboarding for user {target_user_id}")
        
        # Initialize onboarding (will check if already done by trigger)
        result = initialize_user_onboarding(user_id=target_user_id)
        return ok(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"⚠️ Warning: Failed to initialize onboarding: {str(e)}")
        return ok({"status": "onboarding_failed", "error": str(e)})


@onboarding_bp.route("/set-admin", methods=["POST"])
@require_admin
def set_admin():
    """
    Set a user as admin (admin-only).
    
    **ADMIN ONLY**: This endpoint requires authentication AND admin role.
    Adds 'admin' role to the specified user in role_assignments table.
    
    Headers:
        Authorization: Bearer <supabase_jwt_token>
    
    Body (JSON, required): { "user_id": "uuid" }
        - The user_id to grant admin role to
    """
    try:
        # Get authenticated admin user (for logging/audit)
        admin_user_id = request.environ.get('authenticated_user_id')
        
        data = request.get_json(silent=True) or {}
        target_user_id = data.get("user_id")
        
        if not target_user_id:
            return bad("user_id is required", 400)
        
        print(f"🔐 Admin {admin_user_id} setting admin role for user {target_user_id}")
        
        # Check if user already has admin role
        existing = supabase_client.table("role_assignments")\
            .select("id")\
            .eq("user_id", target_user_id)\
            .eq("role", "admin")\
            .limit(1)\
            .execute()
        
        if existing.data and len(existing.data) > 0:
            return ok({
                "message": "User already has admin role",
                "user_id": target_user_id,
                "already_admin": True
            })
        
        # Insert admin role assignment
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        
        result = supabase_client.table("role_assignments")\
            .insert({
                "user_id": target_user_id,
                "role": "admin",
                "confidence": 1.0,
                "evidence": {
                    "source": "manual",
                    "granted_by": admin_user_id,
                    "granted_at": now_iso
                },
                "created_at": now_iso
            })\
            .execute()
        
        if result.data:
            print(f"✅ Admin role granted to user {target_user_id}")
            return ok({
                "message": "Admin role granted successfully",
                "user_id": target_user_id,
                "role_assignment_id": result.data[0]["id"]
            })
        else:
            return bad("Failed to grant admin role", 500)
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Error setting admin role: {e}")
        return bad(f"Error setting admin role: {str(e)}", 500)


@onboarding_bp.route("/set-user-mode", methods=["POST"])
@require_admin
def set_user_mode():
    """
    Set a user's mode (talent|hirer) (admin-only).
    
    **ADMIN ONLY**: This endpoint requires authentication AND admin role.
    Updates:
    - user_preferences.default_mode + user_preferences.last_mode
    - role_assignments (talent/hirer) so the voice qualification flow stays consistent
    
    Body (JSON, required): { "user_id": "uuid", "mode": "talent" | "hirer" }
    """
    try:
        from datetime import datetime, timezone
        admin_user_id = request.environ.get('authenticated_user_id')

        data = request.get_json(silent=True) or {}
        target_user_id = data.get("user_id")
        mode = (data.get("mode") or "").strip().lower()

        if not target_user_id:
            return bad("user_id is required", 400)
        if mode not in ("talent", "hirer"):
            return bad("mode must be 'talent' or 'hirer'", 400)

        print(f"🔐 Admin {admin_user_id} setting user_mode={mode} for user {target_user_id}")

        # Upsert user_preferences row for the target user
        prefs_existing = supabase_client.table("user_preferences")\
            .select("user_id")\
            .eq("user_id", target_user_id)\
            .limit(1)\
            .execute()

        if prefs_existing.data:
            supabase_client.table("user_preferences")\
                .update({"default_mode": mode, "last_mode": mode})\
                .eq("user_id", target_user_id)\
                .execute()
        else:
            supabase_client.table("user_preferences")\
                .insert({"user_id": target_user_id, "default_mode": mode, "last_mode": mode})\
                .execute()

        # Keep role_assignments aligned (only one of talent/hirer at a time)
        now_iso = datetime.now(timezone.utc).isoformat()
        role_existing = supabase_client.table("role_assignments")\
            .select("id, role")\
            .eq("user_id", target_user_id)\
            .in_("role", ["talent", "hirer"])\
            .order("confidence", desc=True)\
            .limit(1)\
            .execute()

        updated_role_id = None
        if role_existing.data:
            updated_role_id = role_existing.data[0]["id"]
            supabase_client.table("role_assignments")\
                .update({
                    "role": mode,
                    "confidence": 1.0,
                    "evidence": {
                        "source": "manual",
                        "set_by": admin_user_id,
                        "set_at": now_iso,
                        "type": "mode_change"
                    }
                })\
                .eq("id", updated_role_id)\
                .execute()

            # Remove any other stale talent/hirer rows to avoid ambiguity in downstream lookups
            supabase_client.table("role_assignments")\
                .delete()\
                .eq("user_id", target_user_id)\
                .in_("role", ["talent", "hirer"])\
                .neq("id", updated_role_id)\
                .execute()
        else:
            inserted = supabase_client.table("role_assignments")\
                .insert({
                    "user_id": target_user_id,
                    "role": mode,
                    "confidence": 1.0,
                    "evidence": {
                        "source": "manual",
                        "set_by": admin_user_id,
                        "set_at": now_iso,
                        "type": "mode_change"
                    },
                    "created_at": now_iso
                })\
                .execute()
            if inserted.data:
                updated_role_id = inserted.data[0].get("id")

        return ok({
            "message": "User mode updated",
            "user_id": target_user_id,
            "mode": mode,
            "role_assignment_id": updated_role_id
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Error setting user mode: {e}")
        return bad(f"Error setting user mode: {str(e)}", 500)


@onboarding_bp.route("/config", methods=["GET"])
@require_admin
def get_platform_config():
    """Read admin-managed configuration values."""
    enabled, updated_at, updated_by = get_bool_config("elevenlabs_output_enabled", default=False)
    no_answer_retries_enabled, retries_updated_at, retries_updated_by = get_bool_config(
        "voice_no_answer_retries_enabled", default=True
    )
    vad_threshold, vad_threshold_updated_at, vad_threshold_updated_by = get_number_config(
        "voice_vad_threshold", default=0.5
    )
    vad_prefix_padding_ms, vad_prefix_updated_at, vad_prefix_updated_by = get_number_config(
        "voice_vad_prefix_padding_ms", default=300
    )
    vad_silence_duration_ms, vad_silence_updated_at, vad_silence_updated_by = get_number_config(
        "voice_vad_silence_duration_ms", default=900
    )
    vad_idle_timeout_ms, vad_idle_updated_at, vad_idle_updated_by = get_number_config(
        "voice_vad_idle_timeout_ms", default=30000
    )
    playback_input_cooldown_ms, playback_cooldown_updated_at, playback_cooldown_updated_by = get_number_config(
        "voice_playback_input_cooldown_ms", default=1200
    )
    end_call_grace_ms, end_call_grace_updated_at, end_call_grace_updated_by = get_number_config(
        "voice_end_call_grace_ms", default=1800
    )
    elevenlabs_preflight_timeout_ms, preflight_timeout_updated_at, preflight_timeout_updated_by = get_number_config(
        "voice_elevenlabs_preflight_timeout_ms", default=200
    )
    talent_greeting, talent_prompt_updated_at, talent_prompt_updated_by = get_string_config(
        "voice_prompt_talent_greeting",
        "Hi, this is A I Dan from ExecFlex. I noticed you just signed up looking for executive opportunities. Have I caught you at a bad time?",
    )
    company_greeting, company_prompt_updated_at, company_prompt_updated_by = get_string_config(
        "voice_prompt_company_greeting",
        "Hello, this is A I Dan from ExecFlex. I noticed you just signed up looking for executive talent for your organization. Have I caught you at a bad time?",
    )
    fallback_greeting, fallback_prompt_updated_at, fallback_prompt_updated_by = get_string_config(
        "voice_prompt_fallback_greeting",
        "Hello, this is A I Dan from ExecFlex. I noticed you just signed up. Are you looking to hire executive talent, or are you an executive looking for opportunities?",
    )
    general_prompt, general_prompt_updated_at, general_prompt_updated_by = get_string_config(
        "voice_prompt_general_system",
        "CONVERSATION STYLE:\n- Be warm, professional, and concise\n- Ask ONE question at a time\n- Keep responses under 20 seconds when spoken (about 50-70 words max)\n- Listen actively and acknowledge what the user says\n- Don't repeat questions that have been answered\n\nCONVERSATION GOALS:\n1. Confirm their intent (hiring vs job seeking)\n2. Understand their motivation (why ExecFlex, why now)\n3. Learn about role preferences (titles, industries)\n4. Understand location and availability preferences\n5. Identify any constraints or deal-breakers\n6. Be witty.\n7. To progress up the levels of conversation from cliche, to facts, to opinions, to feelings, to needs/identity (dreams)\n\nIMPORTANT RULES:\n- Never ask for information already provided\n- If the user wants to end the call, thank them politely and close\n- After 8-10 minutes or when enough info is gathered, begin closing the conversation\n- Be natural and conversational, not robotic\n- When the call has clearly concluded, call the end_call tool exactly once.\n- Do not repeat goodbye lines in a loop.\n- Use Mirroring if they dont seem quite finished. Repeat back the last few words of what they said without embellishment in an upward tone.\n- Use Labelling of the potential emption, if they express an opinion or feeling. e.g. 'That sounds like it was exciting!'",
    )
    return ok({
        "configuration": {
            "elevenlabs_output_enabled": enabled,
            "voice_no_answer_retries_enabled": no_answer_retries_enabled,
            "voice_vad_threshold": vad_threshold,
            "voice_vad_prefix_padding_ms": int(vad_prefix_padding_ms),
            "voice_vad_silence_duration_ms": int(vad_silence_duration_ms),
            "voice_vad_idle_timeout_ms": int(vad_idle_timeout_ms),
            "voice_playback_input_cooldown_ms": int(playback_input_cooldown_ms),
            "voice_end_call_grace_ms": int(end_call_grace_ms),
            "voice_elevenlabs_preflight_timeout_ms": int(elevenlabs_preflight_timeout_ms),
            "voice_prompt_talent_greeting": talent_greeting,
            "voice_prompt_company_greeting": company_greeting,
            "voice_prompt_fallback_greeting": fallback_greeting,
            "voice_prompt_general_system": general_prompt,
            "updated_at": max(
                [
                    x for x in [
                        updated_at,
                        retries_updated_at,
                        vad_threshold_updated_at,
                        vad_prefix_updated_at,
                        vad_silence_updated_at,
                        vad_idle_updated_at,
                        playback_cooldown_updated_at,
                        end_call_grace_updated_at,
                        preflight_timeout_updated_at,
                        talent_prompt_updated_at,
                        company_prompt_updated_at,
                        fallback_prompt_updated_at,
                        general_prompt_updated_at,
                    ] if x
                ] or [None]
            ),
            "updated_by": (
                updated_by
                or retries_updated_by
                or vad_threshold_updated_by
                or vad_prefix_updated_by
                or vad_silence_updated_by
                or vad_idle_updated_by
                or playback_cooldown_updated_by
                or end_call_grace_updated_by
                or preflight_timeout_updated_by
                or talent_prompt_updated_by
                or company_prompt_updated_by
                or fallback_prompt_updated_by
                or general_prompt_updated_by
            ),
        }
    })


@onboarding_bp.route("/config", methods=["POST"])
@require_admin
def set_platform_config():
    """Update admin-managed configuration values."""
    try:
        admin_user_id = request.environ.get('authenticated_user_id')
        data = request.get_json(silent=True) or {}
        allowed_keys = {
            "elevenlabs_output_enabled",
            "voice_no_answer_retries_enabled",
            "voice_vad_threshold",
            "voice_vad_prefix_padding_ms",
            "voice_vad_silence_duration_ms",
            "voice_vad_idle_timeout_ms",
            "voice_playback_input_cooldown_ms",
            "voice_end_call_grace_ms",
            "voice_elevenlabs_preflight_timeout_ms",
            "voice_prompt_talent_greeting",
            "voice_prompt_company_greeting",
            "voice_prompt_fallback_greeting",
            "voice_prompt_general_system",
        }
        provided = [k for k in allowed_keys if k in data]
        if not provided:
            return bad("At least one configuration field is required", 400)

        if "elevenlabs_output_enabled" in data and not isinstance(data.get("elevenlabs_output_enabled"), bool):
            return bad("elevenlabs_output_enabled must be a boolean", 400)
        if "voice_no_answer_retries_enabled" in data and not isinstance(data.get("voice_no_answer_retries_enabled"), bool):
            return bad("voice_no_answer_retries_enabled must be a boolean", 400)

        def _validate_number(name: str, min_value: float, max_value: float, integer: bool = False):
            if name not in data:
                return None
            value = data.get(name)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"{name} must be a number")
            numeric = float(value)
            if numeric < min_value or numeric > max_value:
                raise ValueError(f"{name} must be between {min_value} and {max_value}")
            if integer and int(numeric) != numeric:
                raise ValueError(f"{name} must be an integer")
            return int(numeric) if integer else numeric

        try:
            voice_vad_threshold = _validate_number("voice_vad_threshold", 0.1, 0.95)
            voice_vad_prefix_padding_ms = _validate_number("voice_vad_prefix_padding_ms", 0, 2000, integer=True)
            voice_vad_silence_duration_ms = _validate_number("voice_vad_silence_duration_ms", 200, 4000, integer=True)
            voice_vad_idle_timeout_ms = _validate_number("voice_vad_idle_timeout_ms", 0, 60000, integer=True)
            voice_playback_input_cooldown_ms = _validate_number("voice_playback_input_cooldown_ms", 0, 5000, integer=True)
            voice_end_call_grace_ms = _validate_number("voice_end_call_grace_ms", 0, 8000, integer=True)
            voice_elevenlabs_preflight_timeout_ms = _validate_number("voice_elevenlabs_preflight_timeout_ms", 100, 3000, integer=True)
        except ValueError as validation_err:
            return bad(str(validation_err), 400)

        def _validate_string(name: str, min_len: int, max_len: int):
            if name not in data:
                return None
            value = data.get(name)
            if not isinstance(value, str):
                raise ValueError(f"{name} must be a string")
            trimmed = value.strip()
            if len(trimmed) < min_len or len(trimmed) > max_len:
                raise ValueError(f"{name} must be between {min_len} and {max_len} characters")
            return trimmed

        try:
            voice_prompt_talent_greeting = _validate_string("voice_prompt_talent_greeting", 10, 500)
            voice_prompt_company_greeting = _validate_string("voice_prompt_company_greeting", 10, 500)
            voice_prompt_fallback_greeting = _validate_string("voice_prompt_fallback_greeting", 10, 500)
            voice_prompt_general_system = _validate_string("voice_prompt_general_system", 50, 12000)
        except ValueError as validation_err:
            return bad(str(validation_err), 400)

        if "elevenlabs_output_enabled" in data:
            set_bool_config(
                key="elevenlabs_output_enabled",
                value=bool(data.get("elevenlabs_output_enabled")),
                updated_by=admin_user_id,
                description="Enable OpenAI text output routed through ElevenLabs realtime TTS for new streaming calls",
            )
        if "voice_no_answer_retries_enabled" in data:
            set_bool_config(
                key="voice_no_answer_retries_enabled",
                value=bool(data.get("voice_no_answer_retries_enabled")),
                updated_by=admin_user_id,
                description="Enable automatic retries for no-answer outbound calls using fixed schedule: 10m, 1h, 6h, 24h, 1w",
            )
        if voice_vad_threshold is not None:
            set_number_config(
                key="voice_vad_threshold",
                value=voice_vad_threshold,
                updated_by=admin_user_id,
                description="OpenAI Realtime server_vad threshold for outbound voice calls",
            )
        if voice_vad_prefix_padding_ms is not None:
            set_number_config(
                key="voice_vad_prefix_padding_ms",
                value=voice_vad_prefix_padding_ms,
                updated_by=admin_user_id,
                description="OpenAI Realtime server_vad prefix padding in milliseconds for outbound voice calls",
            )
        if voice_vad_silence_duration_ms is not None:
            set_number_config(
                key="voice_vad_silence_duration_ms",
                value=voice_vad_silence_duration_ms,
                updated_by=admin_user_id,
                description="OpenAI Realtime server_vad silence duration in milliseconds for outbound voice calls",
            )
        if voice_vad_idle_timeout_ms is not None:
            set_number_config(
                key="voice_vad_idle_timeout_ms",
                value=voice_vad_idle_timeout_ms,
                updated_by=admin_user_id,
                description="OpenAI Realtime server_vad idle timeout in milliseconds for outbound voice calls",
            )
        if voice_playback_input_cooldown_ms is not None:
            set_number_config(
                key="voice_playback_input_cooldown_ms",
                value=voice_playback_input_cooldown_ms,
                updated_by=admin_user_id,
                description="Milliseconds to ignore caller audio immediately after assistant playback completes",
            )
        if voice_end_call_grace_ms is not None:
            set_number_config(
                key="voice_end_call_grace_ms",
                value=voice_end_call_grace_ms,
                updated_by=admin_user_id,
                description="Minimum delay before Twilio hangup after end_call tool to let final audio finish",
            )
        if voice_elevenlabs_preflight_timeout_ms is not None:
            set_number_config(
                key="voice_elevenlabs_preflight_timeout_ms",
                value=voice_elevenlabs_preflight_timeout_ms,
                updated_by=admin_user_id,
                description="ElevenLabs websocket preflight timeout in milliseconds at call start",
            )
        if voice_prompt_talent_greeting is not None:
            set_string_config(
                key="voice_prompt_talent_greeting",
                value=voice_prompt_talent_greeting,
                updated_by=admin_user_id,
                description="Greeting used for outbound calls when signup_mode is talent/job_seeker/executive/candidate",
            )
        if voice_prompt_company_greeting is not None:
            set_string_config(
                key="voice_prompt_company_greeting",
                value=voice_prompt_company_greeting,
                updated_by=admin_user_id,
                description="Greeting used for outbound calls when signup_mode is hirer/talent_seeker/company/client/employer",
            )
        if voice_prompt_fallback_greeting is not None:
            set_string_config(
                key="voice_prompt_fallback_greeting",
                value=voice_prompt_fallback_greeting,
                updated_by=admin_user_id,
                description="Greeting used for outbound calls when signup_mode is unknown",
            )
        if voice_prompt_general_system is not None:
            set_string_config(
                key="voice_prompt_general_system",
                value=voice_prompt_general_system,
                updated_by=admin_user_id,
                description="General outbound call system prompt body appended after mode context and greeting",
            )

        enabled, updated_at, updated_by = get_bool_config("elevenlabs_output_enabled", default=False)
        no_answer_retries_enabled, retries_updated_at, retries_updated_by = get_bool_config(
            "voice_no_answer_retries_enabled", default=True
        )
        vad_threshold, _, _ = get_number_config("voice_vad_threshold", default=0.5)
        vad_prefix_padding_ms, _, _ = get_number_config("voice_vad_prefix_padding_ms", default=300)
        vad_silence_duration_ms, _, _ = get_number_config("voice_vad_silence_duration_ms", default=900)
        vad_idle_timeout_ms, _, _ = get_number_config("voice_vad_idle_timeout_ms", default=30000)
        playback_input_cooldown_ms, _, _ = get_number_config("voice_playback_input_cooldown_ms", default=1200)
        end_call_grace_ms, _, _ = get_number_config("voice_end_call_grace_ms", default=1800)
        elevenlabs_preflight_timeout_ms, _, _ = get_number_config("voice_elevenlabs_preflight_timeout_ms", default=200)
        talent_greeting, _, _ = get_string_config(
            "voice_prompt_talent_greeting",
            "Hi, this is A I Dan from ExecFlex. I noticed you just signed up looking for executive opportunities. Have I caught you at a bad time?",
        )
        company_greeting, _, _ = get_string_config(
            "voice_prompt_company_greeting",
            "Hello, this is A I Dan from ExecFlex. I noticed you just signed up looking for executive talent for your organization. Have I caught you at a bad time?",
        )
        fallback_greeting, _, _ = get_string_config(
            "voice_prompt_fallback_greeting",
            "Hello, this is A I Dan from ExecFlex. I noticed you just signed up. Are you looking to hire executive talent, or are you an executive looking for opportunities?",
        )
        general_prompt, _, _ = get_string_config(
            "voice_prompt_general_system",
            "CONVERSATION STYLE:\n- Be warm, professional, and concise\n- Ask ONE question at a time\n- Keep responses under 20 seconds when spoken (about 50-70 words max)\n- Listen actively and acknowledge what the user says\n- Don't repeat questions that have been answered\n\nCONVERSATION GOALS:\n1. Confirm their intent (hiring vs job seeking)\n2. Understand their motivation (why ExecFlex, why now)\n3. Learn about role preferences (titles, industries)\n4. Understand location and availability preferences\n5. Identify any constraints or deal-breakers\n6. Be witty.\n7. To progress up the levels of conversation from cliche, to facts, to opinions, to feelings, to needs/identity (dreams)\n\nIMPORTANT RULES:\n- Never ask for information already provided\n- If the user wants to end the call, thank them politely and close\n- After 8-10 minutes or when enough info is gathered, begin closing the conversation\n- Be natural and conversational, not robotic\n- When the call has clearly concluded, call the end_call tool exactly once.\n- Do not repeat goodbye lines in a loop.\n- Use Mirroring if they dont seem quite finished. Repeat back the last few words of what they said without embellishment in an upward tone.\n- Use Labelling of the potential emption, if they express an opinion or feeling. e.g. 'That sounds like it was exciting!'",
        )
        return ok({
            "configuration": {
                "elevenlabs_output_enabled": enabled,
                "voice_no_answer_retries_enabled": no_answer_retries_enabled,
                "voice_vad_threshold": vad_threshold,
                "voice_vad_prefix_padding_ms": int(vad_prefix_padding_ms),
                "voice_vad_silence_duration_ms": int(vad_silence_duration_ms),
                "voice_vad_idle_timeout_ms": int(vad_idle_timeout_ms),
                "voice_playback_input_cooldown_ms": int(playback_input_cooldown_ms),
                "voice_end_call_grace_ms": int(end_call_grace_ms),
                "voice_elevenlabs_preflight_timeout_ms": int(elevenlabs_preflight_timeout_ms),
                "voice_prompt_talent_greeting": talent_greeting,
                "voice_prompt_company_greeting": company_greeting,
                "voice_prompt_fallback_greeting": fallback_greeting,
                "voice_prompt_general_system": general_prompt,
                "updated_at": max([x for x in [updated_at, retries_updated_at] if x] or [None]),
                "updated_by": retries_updated_by or updated_by,
            }
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Error updating platform config: {e}")
        return bad(f"Error updating platform config: {str(e)}", 500)


@onboarding_bp.route("/conversations", methods=["GET"])
@require_admin
def get_conversations():
    """
    Get list of outbound qualifying conversations (admin-only).
    
    **ADMIN ONLY**: This endpoint requires authentication AND admin role.
    Returns all outbound call jobs with user information and conversation summaries.
    
    Headers:
        Authorization: Bearer <supabase_jwt_token>
    
    Query Parameters (optional):
        - status: Filter by status (queued, running, succeeded, failed)
        - limit: Limit number of results (default: 100)
        - offset: Pagination offset (default: 0)
    
    Returns:
        {
            "conversations": [
                {
                    "id": "uuid",
                    "phone_e164": "+447700900001",
                    "status": "completed",
                    "attempts": 1,
                    "created_at": "2024-12-15T10:30:00Z",
                    "updated_at": "2024-12-15T10:35:00Z",
                    "next_run_at": null,
                    "last_error": null,
                    "twilio_call_sid": "CA1234567890abcdef",
                    "summary": "Candidate interested in CFO roles...",
                    "user_name": "Sarah Mitchell"
                },
                ...
            ],
            "total": 50
        }
    """
    try:
        # Get query parameters
        status_filter = request.args.get("status")
        limit = int(request.args.get("limit", 100))
        offset = int(request.args.get("offset", 0))
        
        # Build base query
        query = supabase_client.table("outbound_call_jobs").select(
            "id, phone_e164, status, attempts, created_at, updated_at, next_run_at, last_error, twilio_call_sid, interaction_id, user_id",
            count="exact"
        )
        
        # Apply filters
        if status_filter:
            # Map frontend status to database status
            status_map = {
                "pending": "queued",
                "in_progress": "running",
                "completed": "succeeded",
                "failed": "failed"
            }
            db_status = status_map.get(status_filter, status_filter)
            query = query.eq("status", db_status)
        
        # Apply pagination and ordering
        query = query.order("created_at", desc=True).limit(limit).offset(offset)
        
        # Execute query
        result = query.execute()
        
        # Get all user_ids and interaction_ids for batch fetching
        user_ids = [job["user_id"] for job in result.data if job.get("user_id")]
        interaction_ids = [job["interaction_id"] for job in result.data if job.get("interaction_id")]
        
        # Batch fetch user profiles (query each user individually if .in_() doesn't work)
        profiles_map = {}
        if user_ids:
            try:
                # Try using .in_() method (Supabase Python client supports this)
                profiles_result = supabase_client.table("people_profiles").select("user_id, first_name, last_name").in_("user_id", user_ids).execute()
                for profile in (profiles_result.data or []):
                    profiles_map[profile["user_id"]] = profile
            except AttributeError:
                # Fallback: query individually
                for user_id in user_ids:
                    try:
                        profile_result = supabase_client.table("people_profiles").select("user_id, first_name, last_name").eq("user_id", user_id).limit(1).execute()
                        if profile_result.data:
                            profiles_map[user_id] = profile_result.data[0]
                    except Exception:
                        continue
        
        # Batch fetch interaction summaries
        summaries_map = {}
        if interaction_ids:
            try:
                # Try using .in_() method
                interactions_result = supabase_client.table("interactions").select("id, summary_text").in_("id", interaction_ids).execute()
                for interaction in (interactions_result.data or []):
                    summaries_map[interaction["id"]] = interaction.get("summary_text")
            except AttributeError:
                # Fallback: query individually
                for interaction_id in interaction_ids:
                    try:
                        interaction_result = supabase_client.table("interactions").select("id, summary_text").eq("id", interaction_id).limit(1).execute()
                        if interaction_result.data:
                            summaries_map[interaction_id] = interaction_result.data[0].get("summary_text")
                    except Exception:
                        continue
        
        # Transform data to match frontend format
        conversations = []
        for job in result.data:
            # Get user name from people_profiles
            user_name = None
            if job.get("user_id") and job["user_id"] in profiles_map:
                profile = profiles_map[job["user_id"]]
                first_name = profile.get("first_name") or ""
                last_name = profile.get("last_name") or ""
                if first_name or last_name:
                    user_name = f"{first_name} {last_name}".strip()
            
            # Get summary from interactions
            summary = None
            if job.get("interaction_id") and job["interaction_id"] in summaries_map:
                summary = summaries_map[job["interaction_id"]]
            
            # Map database status to frontend status
            status_map = {
                "queued": "pending",
                "running": "in_progress",
                "succeeded": "completed",
                "failed": "failed"
            }
            frontend_status = status_map.get(job["status"], job["status"])
            
            conversations.append({
                "id": job["id"],
                "phone_e164": job["phone_e164"],
                "status": frontend_status,
                "attempts": job["attempts"],
                "created_at": job["created_at"],
                "updated_at": job["updated_at"],
                "next_run_at": job.get("next_run_at"),
                "last_error": job.get("last_error"),
                "twilio_call_sid": job.get("twilio_call_sid"),
                "summary": summary,
                "user_name": user_name
            })
        
        return ok({
            "conversations": conversations,
            "total": result.count or len(conversations)
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Error fetching conversations: {str(e)}")
        return bad(f"Failed to fetch conversations: {str(e)}", 500)


@onboarding_bp.route("/conversations/<conversation_id>", methods=["GET"])
@require_admin
def get_conversation_details(conversation_id: str):
    """
    Get detailed conversation data for a specific conversation (admin-only).
    
    **ADMIN ONLY**: This endpoint requires authentication AND admin role.
    Returns conversation turns, transcript, and full interaction details.
    
    Headers:
        Authorization: Bearer <supabase_jwt_token>
    
    Returns:
        {
            "conversation": {
                "id": "uuid",
                "phone_e164": "+447700900001",
                "status": "completed",
                "interaction_id": "uuid",
                "transcript": "Full transcript text...",
                "summary": "Summary text...",
                "turns": [
                    {
                        "speaker": "assistant",
                        "text": "Hello, this is ExecFlex...",
                        "created_at": "2024-12-15T10:30:00Z",
                        "turn_sequence": 1
                    },
                    ...
                ]
            }
        }
    """
    try:
        # Get the job
        job_result = supabase_client.table("outbound_call_jobs")\
            .select("id, phone_e164, status, interaction_id, user_id, twilio_call_sid, created_at, updated_at")\
            .eq("id", conversation_id)\
            .limit(1)\
            .execute()
        
        if not job_result.data or len(job_result.data) == 0:
            return bad("Conversation not found", 404)
        
        job = job_result.data[0]
        interaction_id = job.get("interaction_id")
        
        # Get interaction details (transcript, summary)
        interaction_data = {}
        if interaction_id:
            try:
                interaction_result = supabase_client.table("interactions")\
                    .select("id, transcript_text, summary_text, started_at, ended_at, artifacts")\
                    .eq("id", interaction_id)\
                    .limit(1)\
                    .execute()
                
                if interaction_result.data:
                    interaction_data = interaction_result.data[0]
            except Exception as e:
                print(f"⚠️ Error fetching interaction: {e}")
        
        # Get conversation turns (if interaction_turns table exists)
        turns = []
        if interaction_id:
            try:
                # Check if interaction_turns table exists by trying to query it
                turns_result = supabase_client.table("interaction_turns")\
                    .select("speaker, text, created_at, turn_sequence, artifacts_json")\
                    .eq("interaction_id", interaction_id)\
                    .order("turn_sequence", desc=False)\
                    .execute()
                
                if turns_result.data:
                    turns = [
                        {
                            "speaker": turn.get("speaker"),
                            "text": turn.get("text"),
                            "created_at": turn.get("created_at"),
                            "turn_sequence": turn.get("turn_sequence"),
                            "artifacts": turn.get("artifacts_json")
                        }
                        for turn in turns_result.data
                    ]
            except Exception as e:
                # Table might not exist or error - that's okay, we'll use transcript
                print(f"⚠️ Could not fetch turns (table may not exist): {e}")
        
        # Map database status to frontend status
        status_map = {
            "queued": "pending",
            "running": "in_progress",
            "succeeded": "completed",
            "failed": "failed"
        }
        frontend_status = status_map.get(job["status"], job["status"])
        
        return ok({
            "conversation": {
                "id": job["id"],
                "phone_e164": job["phone_e164"],
                "status": frontend_status,
                "interaction_id": interaction_id,
                "twilio_call_sid": job.get("twilio_call_sid"),
                "created_at": job["created_at"],
                "updated_at": job["updated_at"],
                "transcript": interaction_data.get("transcript_text"),
                "summary": interaction_data.get("summary_text"),
                "turns": turns,
                "started_at": interaction_data.get("started_at"),
                "ended_at": interaction_data.get("ended_at"),
                "artifacts": interaction_data.get("artifacts")
            }
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Error fetching conversation details: {str(e)}")
        return bad(f"Failed to fetch conversation details: {str(e)}", 500)


@onboarding_bp.route("/list-users", methods=["GET"])
@require_admin
def list_users():
    """
    Get list of all users with their roles (admin-only).
    
    **ADMIN ONLY**: This endpoint requires authentication AND admin role.
    Returns users from auth.users who have entries in people_profiles (i.e., have completed onboarding).
    Only shows users who have profiles in our system, not just auth.users entries.
    Includes their associated roles from role_assignments.
    
    Headers:
        Authorization: Bearer <supabase_jwt_token>
    
    Query Parameters (optional):
        - limit: Limit number of results (default: 100)
        - offset: Pagination offset (default: 0)
    
    Returns:
        {
            "users": [
                {
                    "id": "uuid",
                    "email": "user@example.com",
                    "phone": "+1234567890",
                    "created_at": "2024-12-15T10:30:00Z",
                    "last_sign_in_at": "2024-12-16T10:30:00Z",
                    "roles": ["hirer", "admin"]
                },
                ...
            ],
            "total": 50
        }
    """
    try:
        def _clean_text(value):
            if value is None:
                return None
            text = str(value).strip()
            if not text:
                return None
            if text.lower() in ("null", "none", "undefined", "n/a"):
                return None
            return text

        include_debug = str(request.args.get("debug", "")).strip().lower() in ("1", "true", "yes")

        # Get query parameters
        limit = int(request.args.get("limit", 100))
        offset = int(request.args.get("offset", 0))
        
        # Use Supabase Admin API to list users
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json"
        }
        
        list_url = f"{SUPABASE_URL}/auth/v1/admin/users"
        params = {"per_page": limit, "page": (offset // limit) + 1 if limit > 0 else 1}
        
        response = requests.get(list_url, headers=headers, params=params)
        
        if response.status_code != 200:
            return bad(f"Error querying auth users: {response.status_code}", 500)
        
        auth_users = response.json().get("users", [])
        
        # Get all user IDs to check which ones have people_profiles
        user_ids = [user["id"] for user in auth_users]
        
        # Filter to only users who have entries in people_profiles
        # This ensures we only show users who have actually completed onboarding
        # or have profiles in our system (not just auth.users entries)
        users_with_profiles = set()
        if user_ids:
            try:
                # Batch fetch user_ids that have people_profiles
                profiles_result = supabase_client.table("people_profiles")\
                    .select("user_id")\
                    .in_("user_id", user_ids)\
                    .execute()
                
                users_with_profiles = {profile["user_id"] for profile in (profiles_result.data or [])}
            except AttributeError:
                # Fallback: query individually
                for user_id in user_ids:
                    try:
                        profile_result = supabase_client.table("people_profiles")\
                            .select("user_id")\
                            .eq("user_id", user_id)\
                            .limit(1)\
                            .execute()
                        if profile_result.data:
                            users_with_profiles.add(user_id)
                    except Exception:
                        continue
        
        # Filter auth_users to only those with profiles
        filtered_auth_users = [user for user in auth_users if user["id"] in users_with_profiles]
        
        # Get all filtered user IDs to fetch roles
        filtered_user_ids = [user["id"] for user in filtered_auth_users]
        
        # Fetch profile names and LinkedIn URLs for filtered users
        names_map = {}
        linkedin_url_map = {}
        if filtered_user_ids:
            try:
                profiles_result = supabase_client.table("people_profiles")\
                    .select("user_id, first_name, last_name, linkedin_profile_url")\
                    .in_("user_id", filtered_user_ids)\
                    .execute()

                for profile in (profiles_result.data or []):
                    user_id = profile.get("user_id")
                    first_name = _clean_text(profile.get("first_name")) or ""
                    last_name = _clean_text(profile.get("last_name")) or ""
                    full_name = f"{first_name} {last_name}".strip()
                    if user_id and full_name:
                        names_map[user_id] = full_name
                    linkedin_profile_url = _clean_text(profile.get("linkedin_profile_url"))
                    if user_id and linkedin_profile_url:
                        linkedin_url_map[user_id] = linkedin_profile_url
            except AttributeError:
                # Fallback: query individually
                for user_id in filtered_user_ids:
                    try:
                        profile_result = supabase_client.table("people_profiles")\
                            .select("first_name, last_name, linkedin_profile_url")\
                            .eq("user_id", user_id)\
                            .limit(1)\
                            .execute()
                        if profile_result.data:
                            profile = profile_result.data[0] or {}
                            first_name = _clean_text(profile.get("first_name")) or ""
                            last_name = _clean_text(profile.get("last_name")) or ""
                            full_name = f"{first_name} {last_name}".strip()
                            if full_name:
                                names_map[user_id] = full_name
                            linkedin_profile_url = _clean_text(profile.get("linkedin_profile_url"))
                            if linkedin_profile_url:
                                linkedin_url_map[user_id] = linkedin_profile_url
                    except Exception:
                        continue

        # Fetch phone identities as fallback when auth.users phone is missing
        phone_map = {}
        if filtered_user_ids:
            channel_priority = {"voice": 0, "sms": 1, "whatsapp": 2}
            try:
                identities_result = supabase_client.table("channel_identities")\
                    .select("user_id, channel, value")\
                    .in_("user_id", filtered_user_ids)\
                    .in_("channel", ["voice", "sms", "whatsapp"])\
                    .execute()

                for identity in (identities_result.data or []):
                    user_id = identity.get("user_id")
                    channel = (identity.get("channel") or "").strip().lower()
                    value = _clean_text(identity.get("value"))
                    if not user_id or not value:
                        continue
                    current = phone_map.get(user_id)
                    current_priority = channel_priority.get(current["channel"], 999) if current else 999
                    this_priority = channel_priority.get(channel, 999)
                    if not current or this_priority < current_priority:
                        phone_map[user_id] = {"channel": channel, "value": value}
            except AttributeError:
                # Fallback: query individually
                for user_id in filtered_user_ids:
                    try:
                        identities_result = supabase_client.table("channel_identities")\
                            .select("channel, value")\
                            .eq("user_id", user_id)\
                            .in_("channel", ["voice", "sms", "whatsapp"])\
                            .execute()
                        best_identity = None
                        best_priority = 999
                        for identity in (identities_result.data or []):
                            channel = (identity.get("channel") or "").strip().lower()
                            value = _clean_text(identity.get("value"))
                            if not value:
                                continue
                            this_priority = channel_priority.get(channel, 999)
                            if this_priority < best_priority:
                                best_identity = value
                                best_priority = this_priority
                        if best_identity:
                            phone_map[user_id] = {"channel": "fallback", "value": best_identity}
                    except Exception:
                        continue

        # Fetch roles for filtered users
        roles_map = {}
        if filtered_user_ids:
            try:
                # Try batch fetch with .in_()
                roles_result = supabase_client.table("role_assignments")\
                    .select("user_id, role")\
                    .in_("user_id", filtered_user_ids)\
                    .execute()
                
                for role_assignment in (roles_result.data or []):
                    user_id = role_assignment["user_id"]
                    if user_id not in roles_map:
                        roles_map[user_id] = []
                    roles_map[user_id].append(role_assignment["role"])
            except AttributeError:
                # Fallback: query individually
                for user_id in filtered_user_ids:
                    try:
                        role_result = supabase_client.table("role_assignments")\
                            .select("role")\
                            .eq("user_id", user_id)\
                            .execute()
                        if role_result.data:
                            roles_map[user_id] = [r["role"] for r in role_result.data]
                    except Exception:
                        continue

        # Fetch latest outbound phone used for onboarding calls as fallback
        outbound_phone_map = {}
        if filtered_user_ids:
            try:
                jobs_result = supabase_client.table("outbound_call_jobs")\
                    .select("user_id, phone_e164, created_at")\
                    .in_("user_id", filtered_user_ids)\
                    .execute()

                latest_by_user = {}
                for row in (jobs_result.data or []):
                    user_id = row.get("user_id")
                    phone = _clean_text(row.get("phone_e164"))
                    created_at = _clean_text(row.get("created_at")) or ""
                    if not user_id or not phone:
                        continue
                    current = latest_by_user.get(user_id)
                    if not current or created_at > current["created_at"]:
                        latest_by_user[user_id] = {"created_at": created_at, "phone": phone}

                for user_id, value in latest_by_user.items():
                    outbound_phone_map[user_id] = value["phone"]
            except Exception:
                pass

        # Fetch LinkedIn auth status for filtered users
        linkedin_connected_map = {}
        if filtered_user_ids:
            try:
                linkedin_result = supabase_client.table("linkedin_connections")\
                    .select("user_id, status")\
                    .in_("user_id", filtered_user_ids)\
                    .execute()

                for row in (linkedin_result.data or []):
                    user_id = row.get("user_id")
                    status = (row.get("status") or "").strip().lower()
                    if user_id:
                        linkedin_connected_map[user_id] = status == "active"
            except AttributeError:
                # Fallback: query individually
                for user_id in filtered_user_ids:
                    try:
                        linkedin_result = supabase_client.table("linkedin_connections")\
                            .select("status")\
                            .eq("user_id", user_id)\
                            .limit(1)\
                            .execute()
                        if linkedin_result.data:
                            status = (linkedin_result.data[0].get("status") or "").strip().lower()
                            linkedin_connected_map[user_id] = status == "active"
                    except Exception:
                        continue

        # Fetch user mode from user_preferences for filtered users (last_mode > default_mode)
        modes_map = {}
        if filtered_user_ids:
            try:
                prefs_result = supabase_client.table("user_preferences")\
                    .select("user_id, last_mode, default_mode")\
                    .in_("user_id", filtered_user_ids)\
                    .execute()

                for row in (prefs_result.data or []):
                    user_id = row.get("user_id")
                    candidate = (row.get("last_mode") or row.get("default_mode") or "")
                    candidate = str(candidate).strip().lower()
                    if user_id and candidate in ("talent", "hirer"):
                        modes_map[user_id] = candidate
            except AttributeError:
                # Fallback: query individually
                for user_id in filtered_user_ids:
                    try:
                        prefs_result = supabase_client.table("user_preferences")\
                            .select("last_mode, default_mode")\
                            .eq("user_id", user_id)\
                            .limit(1)\
                            .execute()
                        if prefs_result.data:
                            row = prefs_result.data[0] or {}
                            candidate = (row.get("last_mode") or row.get("default_mode") or "")
                            candidate = str(candidate).strip().lower()
                            if candidate in ("talent", "hirer"):
                                modes_map[user_id] = candidate
                    except Exception:
                        continue
        
        # Transform data to match frontend format
        users = []
        for auth_user in filtered_auth_users:
            user_id = auth_user["id"]
            roles = roles_map.get(user_id, []) or []
            metadata = auth_user.get("user_metadata") or {}
            identities = auth_user.get("identities") or []

            first_identity_data = {}
            for identity in identities:
                if isinstance(identity, dict):
                    identity_data = identity.get("identity_data")
                    if isinstance(identity_data, dict):
                        first_identity_data = identity_data
                        break

            metadata_full_name = (
                _clean_text(metadata.get("full_name"))
                or " ".join(
                    part for part in [_clean_text(metadata.get("first_name")), _clean_text(metadata.get("last_name"))] if part
                ).strip()
            )
            identity_full_name = (
                _clean_text(first_identity_data.get("full_name"))
                or _clean_text(first_identity_data.get("name"))
                or " ".join(
                    part for part in [_clean_text(first_identity_data.get("first_name")), _clean_text(first_identity_data.get("last_name"))] if part
                ).strip()
            )
            email = _clean_text(auth_user.get("email"))
            identity_email = _clean_text(first_identity_data.get("email"))
            display_name = (
                names_map.get(user_id)
                or (metadata_full_name if isinstance(metadata_full_name, str) and metadata_full_name.strip() else None)
                or (identity_full_name if isinstance(identity_full_name, str) and identity_full_name.strip() else None)
                or (email.split("@")[0] if isinstance(email, str) and "@" in email else None)
                or (identity_email.split("@")[0] if isinstance(identity_email, str) and "@" in identity_email else None)
            )
            phone_candidates = {
                "auth.phone": _clean_text(auth_user.get("phone")),
                "metadata.phone": _clean_text(metadata.get("phone")),
                "metadata.phone_number": _clean_text(metadata.get("phone_number")),
                "identity.phone": _clean_text(first_identity_data.get("phone")),
                "identity.phone_number": _clean_text(first_identity_data.get("phone_number")),
                "channel_identities": _clean_text((phone_map.get(user_id) or {}).get("value")),
                "outbound_call_jobs.phone_e164": _clean_text(outbound_phone_map.get(user_id)),
            }
            phone = None
            phone_source = None
            for source_key in (
                "auth.phone",
                "metadata.phone",
                "metadata.phone_number",
                "identity.phone",
                "identity.phone_number",
                "channel_identities",
                "outbound_call_jobs.phone_e164",
            ):
                candidate = phone_candidates.get(source_key)
                if candidate:
                    phone = candidate
                    phone_source = source_key
                    break
            has_linkedin_identity = False
            for identity in identities:
                if not isinstance(identity, dict):
                    continue
                provider = str(identity.get("provider") or "").strip().lower()
                if provider == "linkedin_oidc" or provider == "linkedin":
                    has_linkedin_identity = True
                    break
            # Fallback to role_assignments if user_preferences missing
            fallback_mode = None
            if "hirer" in roles:
                fallback_mode = "hirer"
            elif "talent" in roles:
                fallback_mode = "talent"
            user_row = {
                "id": user_id,
                "name": display_name,
                "email": email or identity_email,
                "phone": phone,
                "created_at": auth_user.get("created_at"),
                "last_sign_in_at": auth_user.get("last_sign_in_at"),
                "roles": roles,
                "linkedin_connected": linkedin_connected_map.get(user_id, False) or has_linkedin_identity,
                "linkedin_profile_url": linkedin_url_map.get(user_id),
                "user_mode": modes_map.get(user_id) or fallback_mode
            }
            if include_debug:
                user_row["phone_debug_source"] = phone_source
                user_row["phone_debug_candidates"] = phone_candidates
                user_row["phone_debug_value"] = phone
                user_row["phone_debug_length"] = len(phone) if isinstance(phone, str) else 0
                user_row["phone_debug_char_codes"] = (
                    [ord(ch) for ch in phone[:24]] if isinstance(phone, str) else []
                )

            if not phone:
                print(
                    "⚠️ Admin list-users missing phone",
                    {
                        "user_id": user_id,
                        "email": email or identity_email,
                        "phone_candidates": phone_candidates,
                    },
                )

            users.append(user_row)
        
        return ok({
            "users": users,
            "total": len(users)  # Note: Supabase Admin API doesn't return total count easily
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Error listing users: {str(e)}")
        return bad(f"Failed to list users: {str(e)}", 500)


@onboarding_bp.route("/delete-user", methods=["POST"])
@require_admin
def delete_user():
    """
    Delete a user and all associated data (admin-only).
    
    **ADMIN ONLY**: This endpoint requires authentication AND admin role.
    Performs comprehensive cleanup of all user-related data including:
    - outbound_call_jobs, thread_participants, threads, organization_members
    - opportunities, match_suggestions, channel_identities
    - role_assignments, user_preferences, people_profiles
    - organizations (sets created_by_user_id to NULL)
    - auth.users (via Supabase Admin API)
    
    Note: Interactions are append-only (event sourcing) and remain as historical records.
    
    Headers:
        Authorization: Bearer <supabase_jwt_token>
    
    Body (JSON, required): { "user_id": "uuid" } OR { "phone": "+1234567890" }
        - user_id: UUID of the user to delete
        - phone: Phone number (E.164 format) to find and delete user by
    
    Returns:
        JSON response with deletion status and details
    """
    try:
        # Get authenticated admin user (for logging/audit)
        admin_user_id = request.environ.get('authenticated_user_id')
        
        data = request.get_json(silent=True) or {}
        target_user_id = data.get("user_id")
        phone = data.get("phone")
        
        if not target_user_id and not phone:
            return bad("Either user_id or phone is required", 400)
        
        # If phone provided, find user_id first
        if phone and not target_user_id:
            print(f"🔐 Admin {admin_user_id} searching for user by phone: {phone}")
            
            # Search for user by phone in auth.users (requires admin API)
            headers = {
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json"
            }
            
            list_url = f"{SUPABASE_URL}/auth/v1/admin/users"
            response = requests.get(list_url, headers=headers, params={"phone": phone})
            
            if response.status_code != 200:
                return bad(f"Error querying auth users: {response.status_code}", 500)
            
            users = response.json().get("users", [])
            if not users:
                return bad(f"User with phone {phone} not found", 404)
            
            target_user_id = users[0]["id"]
            print(f"  Found user ID: {target_user_id}")
        
        if not target_user_id:
            return bad("Could not determine user_id", 400)
        
        print(f"🔐 Admin {admin_user_id} deleting user {target_user_id}")
        
        deletion_results = {
            "user_id": target_user_id,
            "deleted_tables": [],
            "errors": [],
            "warnings": []
        }
        
        # Delete from related tables (order matters due to foreign keys)
        tables_to_delete = [
            ("outbound_call_jobs", "user_id"),
            ("thread_participants", "user_id"),
            ("organization_members", "user_id"),
            ("opportunities", "created_by_user_id"),
            ("match_suggestions", "suggested_user_id"),
            ("channel_identities", "user_id"),
            ("linkedin_connections", "user_id"),
            ("role_assignments", "user_id"),
            ("user_preferences", "user_id"),
            ("people_profiles", "user_id"),
        ]
        
        for table_name, column_name in tables_to_delete:
            try:
                result = supabase_client.table(table_name).delete().eq(column_name, target_user_id).execute()
                deletion_results["deleted_tables"].append(table_name)
                print(f"  ✓ Deleted {table_name}")
            except Exception as e:
                error_msg = str(e)
                if "does not exist" in error_msg.lower():
                    pass  # Table doesn't exist, skip silently
                else:
                    deletion_results["errors"].append(f"{table_name}: {error_msg}")
                    print(f"  ⚠️  Error deleting {table_name}: {e}")
        
        # Mark threads as inactive (interactions are append-only)
        try:
            supabase_client.table("threads").update({"active": False}).or_(
                f"primary_user_id.eq.{target_user_id},owner_user_id.eq.{target_user_id}"
            ).execute()
            deletion_results["deleted_tables"].append("threads (marked inactive)")
            print("  ✓ Marked threads as inactive")
        except Exception as e:
            deletion_results["warnings"].append(f"threads: {e}")
            print(f"  ⚠️  Error updating threads: {e}")
        
        deletion_results["warnings"].append("Interactions are append-only and remain as historical records")
        print("  ℹ️  Interactions are append-only and remain as historical records")
        
        # Update organizations (set created_by_user_id to NULL)
        try:
            supabase_client.table("organizations").update({"created_by_user_id": None}).eq(
                "created_by_user_id", target_user_id
            ).execute()
            deletion_results["deleted_tables"].append("organizations (updated)")
            print("  ✓ Updated organizations (set created_by_user_id to NULL)")
        except Exception as e:
            deletion_results["warnings"].append(f"organizations: {e}")
            print(f"  ⚠️  Error updating organizations: {e}")
        
        # Finally, delete auth user (requires admin API)
        # Note: Even if this fails, we've deleted all application data, so the user
        # won't appear in list-users (which filters by people_profiles)
        print("\nDeleting auth user...")
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json"
        }
        delete_url = f"{SUPABASE_URL}/auth/v1/admin/users/{target_user_id}"
        response = requests.delete(delete_url, headers=headers)
        
        if response.status_code == 200:
            deletion_results["auth_user_deleted"] = True
            print("  ✓ Deleted auth user")
        elif response.status_code == 404:
            deletion_results["auth_user_deleted"] = False
            deletion_results["warnings"].append("Auth user not found (may have been already deleted)")
            print("  ⚠️  Auth user not found (may have been already deleted)")
        else:
            error_response = response.json() if response.text else {}
            error_msg = error_response.get("message", response.text)
            
            if "append-only" in error_msg.lower() or "interactions" in error_msg.lower():
                deletion_results["auth_user_deleted"] = False
                deletion_results["warnings"].append(
                    "Cannot delete auth user: interactions are append-only (event sourcing). "
                    "This is expected - interactions remain as historical records. "
                    "User will not appear in admin list (filtered by people_profiles)."
                )
                print("  ⚠️  Cannot delete auth user: interactions are append-only")
            else:
                deletion_results["auth_user_deleted"] = False
                deletion_results["warnings"].append(
                    f"Auth user deletion failed: {response.status_code} - {error_msg}. "
                    "User will not appear in admin list (filtered by people_profiles)."
                )
                print(f"  ⚠️  Could not delete auth user: {response.status_code} - {error_msg}")
                print(f"  Response body: {response.text}")
        
        # Consider deletion successful if all application data is deleted
        # Even if auth.users deletion fails, user won't appear in admin list
        deletion_results["success"] = len(deletion_results["errors"]) == 0
        deletion_results["message"] = (
            f"User {target_user_id} deletion completed. "
            "All application data removed. User will no longer appear in admin list."
        )
        
        return ok(deletion_results)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Error deleting user: {e}")
        return bad(f"Error deleting user: {str(e)}", 500)


# DEPRECATED ENDPOINTS REMOVED:
# All conversation handling is unified:
# - Outbound: /voice/stream (realtime streaming via /voice/ws)
# - Status: /voice/status (handles all call status updates)
# The worker uses /voice/stream and /voice/status directly

@onboarding_bp.route("/status", methods=["POST"])
def onboarding_status():
    """
    DEPRECATED: Twilio status callback webhook for onboarding calls.
    Use /voice/status instead.
    
    Kept for backward compatibility only.
    """
    call_sid = request.form.get("CallSid")
    call_status = request.form.get("CallStatus")  # queued, ringing, in-progress, completed, failed, busy, no-answer, canceled
    call_duration = request.form.get("CallDuration")  # seconds, only for completed
    from_number = request.form.get("From")
    to_number = request.form.get("To")
    
    if not call_sid:
        return Response("Missing CallSid", status=400), 400
    
    try:
        from config.clients import supabase_client
        from datetime import datetime
        
        if not supabase_client:
            print("⚠️ Supabase client not available for status update")
            return Response("OK", status=200), 200
        
        # Find job by call_sid
        job_resp = supabase_client.table("outbound_call_jobs")\
            .select("*")\
            .eq("twilio_call_sid", call_sid)\
            .limit(1)\
            .execute()
        
        if not job_resp.data:
            print(f"⚠️ No job found for call_sid: {call_sid}")
            return Response("OK", status=200), 200
        
        job = job_resp.data[0]
        job_id = job["id"]
        interaction_id = job.get("interaction_id")
        
        # Map Twilio status to job status
        status_map = {
            "completed": "succeeded",
            "failed": "failed",
            "busy": "failed",
            "no-answer": "failed",
            "canceled": "failed"
        }
        job_status = status_map.get(call_status, job.get("status", "running"))
        
        # Update job
        from datetime import timezone
        now_iso = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()
        update_data = {
            "status": job_status,
            "updated_at": now_iso,
            "artifacts": {
                **job.get("artifacts", {}),
                "call_status": call_status,
                "call_duration": call_duration,
                "from_number": from_number,
                "to_number": to_number,
                "status_updated_at": now_iso
            }
        }
        
        supabase_client.table("outbound_call_jobs")\
            .update(update_data)\
            .eq("id", job_id)\
            .execute()
        
        # Update interaction
        # Note: interactions table doesn't have 'status' - use ended_at to indicate completion
        if interaction_id:
            interaction_update = {
                "raw_payload": request.form.to_dict()
            }
            
            # Set ended_at if call completed or failed
            if call_status in ["completed", "failed", "busy", "no-answer", "canceled"]:
                interaction_update["ended_at"] = datetime.utcnow().isoformat()
            
            # Note: interactions are append-only, so we can't update them
            # Instead, we'll store the status in the job's artifacts
            # The interaction's ended_at will be set when the call completes
            # For now, we'll just update the raw_payload via a direct SQL call if needed
            # But since interactions are append-only, we should create a new interaction record
            # For MVP, we'll just update the job and leave interaction as-is
            print(f"ℹ️  Interaction {interaction_id} status: {call_status} (interactions are append-only)")
        
        print(f"✅ Updated onboarding call status: job_id={job_id}, call_sid={call_sid}, status={call_status}")
        return Response("OK", status=200), 200
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Error updating call status: {e}")
        # Return 200 to prevent Twilio retries
        return Response("OK", status=200), 200


@onboarding_bp.route("/process-jobs", methods=["POST"])
def process_jobs_endpoint():
    """
    Endpoint to trigger job processing (for HTTP-based cron/scheduled tasks).

    **Note:** This endpoint is optional. If using Render Background Workers,
    you can run `python -m workers.call_dispatcher` directly instead.

    Optional query param: limit (default 10)
    """
    # Optional: Add service secret protection here if needed
    limit = int(request.args.get("limit", 10))

    try:
        processed = process_queued_jobs(limit=limit)
        return ok({"processed": processed, "message": f"Processed {processed} jobs"})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return bad(f"Failed to process jobs: {str(e)}", 500)


# =============================================================================
# LinkedIn OAuth Integration Endpoints
# =============================================================================

from utils.auth_helpers import require_auth


@onboarding_bp.route("/linkedin/start", methods=["POST"])
@require_auth
def linkedin_start():
    """
    Start LinkedIn OAuth flow.

    **Auth Required**: User must be authenticated.
    Returns the OAuth authorization URL to redirect the user to.

    Headers:
        Authorization: Bearer <supabase_jwt_token>

    Body (JSON, optional):
        { "redirect_after": "/dashboard" } - URL to redirect to after OAuth

    Returns:
        {
            "url": "https://linkedin.com/oauth/v2/authorization?...",
            "state": "abc123..."
        }
    """
    try:
        from services.linkedin_service import get_oauth_url

        user_id = request.environ.get('authenticated_user_id')
        data = request.get_json(silent=True) or {}
        redirect_after = data.get("redirect_after")

        result = get_oauth_url(user_id, redirect_after)

        print(f"🔗 LinkedIn OAuth started for user {user_id}")
        return ok(result)

    except ValueError as e:
        return bad(str(e), 400)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Error starting LinkedIn OAuth: {e}")
        return bad(f"Failed to start LinkedIn OAuth: {str(e)}", 500)


@onboarding_bp.route("/linkedin/callback", methods=["GET"])
def linkedin_callback():
    """
    LinkedIn OAuth callback handler.

    **No Auth Required**: This is called by LinkedIn redirect, not frontend.
    Validates state, exchanges code for tokens, imports profile data,
    and redirects to frontend completion page.

    Query Parameters:
        - code: Authorization code from LinkedIn
        - state: State parameter for validation
        - error: Error code if OAuth failed
        - error_description: Error description if OAuth failed

    Redirects to frontend with result parameters.
    """
    from flask import redirect
    from urllib.parse import urlencode
    import os

    # Get frontend URL for redirects
    frontend_url = os.getenv("FRONTEND_URL", "https://execflex.ai")

    # Check for OAuth errors
    error = request.args.get("error")
    if error:
        error_desc = request.args.get("error_description", "LinkedIn authorization failed")
        print(f"❌ LinkedIn OAuth error: {error} - {error_desc}")
        params = urlencode({"error": error_desc})
        return redirect(f"{frontend_url}/linkedin-connect?{params}")

    code = request.args.get("code")
    state = request.args.get("state")

    if not code or not state:
        params = urlencode({"error": "Missing authorization code or state"})
        return redirect(f"{frontend_url}/linkedin-connect?{params}")

    try:
        from services.linkedin_service import handle_oauth_callback

        result = handle_oauth_callback(code, state)

        if not result.get("success"):
            error_msg = result.get("error", "OAuth flow failed")
            print(f"❌ LinkedIn OAuth callback failed: {error_msg}")
            params = urlencode({"error": error_msg})
            return redirect(f"{frontend_url}/linkedin-connect?{params}")

        # Success - redirect to completion page
        print(f"✅ LinkedIn OAuth completed for user {result.get('user_id')}")

        params = {
            "success": "true",
            "imported": ",".join(result.get("imported_fields", [])),
            "missing": ",".join(result.get("missing_fields", []))
        }

        # If user has missing fields, go to completion page
        if result.get("missing_fields"):
            return redirect(f"{frontend_url}/profile-completion?{urlencode(params)}")

        # Otherwise, go to intended destination or default
        redirect_after = result.get("redirect_after") or "/find-jobs"
        return redirect(f"{frontend_url}{redirect_after}?linkedin=connected")

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Error in LinkedIn callback: {e}")
        params = urlencode({"error": str(e)})
        return redirect(f"{frontend_url}/linkedin-connect?{params}")


@onboarding_bp.route("/linkedin/status", methods=["GET"])
@require_auth
def linkedin_status():
    """
    Get LinkedIn connection status and profile completion.

    **Auth Required**: User must be authenticated.
    Used by frontend to determine if LinkedIn gate should be shown.

    Headers:
        Authorization: Bearer <supabase_jwt_token>

    Returns:
        {
            "connected": true/false,
            "imported_fields": ["first_name", "headshot_url", ...],
            "missing_fields": ["headline", "location", ...],
            "completion_score": 75,
            "linked_at": "2026-03-02T10:30:00Z",
            "last_sync_at": "2026-03-02T10:30:00Z"
        }
    """
    try:
        from services.linkedin_service import get_connection_status

        user_id = request.environ.get('authenticated_user_id')
        result = get_connection_status(user_id)

        return ok(result)

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Error getting LinkedIn status: {e}")
        return bad(f"Failed to get LinkedIn status: {str(e)}", 500)


@onboarding_bp.route("/linkedin/skip", methods=["POST"])
@require_auth
def linkedin_skip():
    """
    Record that user skipped LinkedIn connection.

    **Auth Required**: User must be authenticated.
    Records skip event for analytics but does not prevent future prompts.

    Headers:
        Authorization: Bearer <supabase_jwt_token>

    Returns:
        { "skipped": true, "missing_fields": ["first_name", ...] }
    """
    try:
        from services.linkedin_service import record_skip_event, get_connection_status

        user_id = request.environ.get('authenticated_user_id')

        # Record the skip
        record_skip_event(user_id)

        # Return current status so frontend knows what to show
        status = get_connection_status(user_id)

        print(f"⏭️ User {user_id} skipped LinkedIn connection")

        return ok({
            "skipped": True,
            "missing_fields": status.get("missing_fields", [])
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Error recording LinkedIn skip: {e}")
        return bad(f"Failed to record skip: {str(e)}", 500)
