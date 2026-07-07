"""
Health check routes + landing page.
"""
import os
from flask import jsonify, send_from_directory, request
from routes import health_bp
from utils.response_helpers import ok


@health_bp.route("/", methods=["GET"])
def root():
    """Serve landing page for browsers, JSON health for API clients."""
    accept = request.headers.get("Accept", "")
    # Render.io / uptime monitors send text/html or */*; API callers send application/json
    if "application/json" in accept and "text/html" not in accept:
        return ok({"status": "healthy", "service": "ExecFlex API", "message": "Service is running"})
    static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
    return send_from_directory(static_dir, "index.html")


@health_bp.route("/health", methods=["GET"])
def health_check():
    """JSON health check endpoint — for monitoring and Ainm integration."""
    return ok({"status": "healthy", "service": "ExecFlex API", "message": "Service is running"})


@health_bp.route("/health/ai-selftest", methods=["GET"])
def health_ai_selftest():
    """Token-free AI diagnostic. Only active when AI_DEBUG_ERRORS=1 (else 404).
    Runs the JD generator on hardcoded synthetic input (no PII, no request data)
    and returns the raw traceback tail on failure so we can pinpoint errors
    without an auth token. Temporary — remove with the rest of the AI_DEBUG
    scaffolding once the AI path is healthy."""
    if os.getenv("AI_DEBUG_ERRORS", "").strip().lower() not in ("1", "true", "yes"):
        return jsonify({"error": "not found"}), 404

    import traceback
    # Safe key fingerprint — no key material revealed, just shape + any non-ascii.
    _k = os.getenv("ANTHROPIC_API_KEY", "")
    _first_bad = next(((i, repr(c)) for i, c in enumerate(_k) if ord(c) > 127), None)
    key_check = {
        "present": bool(_k),
        "len": len(_k),
        "is_ascii": _k.isascii(),
        "first_non_ascii": _first_bad,  # (index, char) or null
    }
    try:
        from services.ai.agent_service import _get_llm_client
        client = _get_llm_client()
        if client is None:
            return ok({"stage": "client", "ok": False, "key_check": key_check, "detail": "LLM client is None (missing key/import)"})
        from agentic_core.agents.recruitment import JDGeneratorAgent
        agent = JDGeneratorAgent(client)
        result = agent.run(
            role_title="Test Engineer",
            company_summary="",
            responsibilities="Own the platform.",
            requirements="5 plus years experience.",
            pay_range_min=80000,
            pay_range_max=100000,
            pay_currency="EUR",
            location="Dublin",
        )
        return ok({
            "stage": "complete",
            "ok": True,
            "key_check": key_check,
            "word_count": getattr(result, "word_count", None),
            "cost_usd": getattr(result, "cost_usd", None),
        })
    except Exception as e:
        return jsonify({
            "ok": False,
            "key_check": key_check,
            "error": f"{type(e).__name__}: {e}",
            "traceback": traceback.format_exc()[-1200:],
        }), 200


@health_bp.route("/health/runtime", methods=["GET"])
def health_runtime():
    """Diagnostic: which commit is live + the process text encoding. No auth, no PII.
    Lets us confirm a deploy landed and whether Python UTF-8 mode is active."""
    import sys
    import locale
    commit = os.getenv("RENDER_GIT_COMMIT", "")
    if not commit:
        try:
            import subprocess
            commit = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL, text=True
            ).strip()
        except Exception:
            commit = "unknown"
    try:
        preferred = locale.getpreferredencoding(False)
    except Exception:
        preferred = "?"
    return ok({
        "commit": commit[:12],
        "python": sys.version.split()[0],
        "utf8_mode": bool(sys.flags.utf8_mode),
        "preferred_encoding": preferred,
        "stdout_encoding": getattr(sys.stdout, "encoding", None),
        "env": {
            "PYTHONUTF8": os.getenv("PYTHONUTF8", "(unset)"),
            "PYTHONIOENCODING": os.getenv("PYTHONIOENCODING", "(unset)"),
            "LANG": os.getenv("LANG", "(unset)"),
            "LC_ALL": os.getenv("LC_ALL", "(unset)"),
        },
    })


@health_bp.route("/submit-brief", methods=["POST"])
def submit_brief():
    """Capture landing page lead submissions. No auth required."""
    data = request.get_json(silent=True) or {}
    email = str(data.get("email", "")).strip()[:200]
    if not email:
        return jsonify({"error": "email required"}), 400

    name = (str(data.get("name", "")).strip()[:200]) or None
    company = (str(data.get("company", "")).strip()[:200]) or None
    message = (str(data.get("message", "")).strip()[:5000]) or None
    source = str(data.get("source", "landing_page")).strip()[:100] or "landing_page"

    # Insert into Supabase
    try:
        from config.clients import supabase_client
        if supabase_client:
            supabase_client.table("inbound_leads").insert({
                "email": email,
                "name": name,
                "company": company,
                "message": message,
                "source": source,
            }).execute()
            print(f"[LEAD] Stored lead: email={email!r} name={name!r} company={company!r} source={source!r}")
        else:
            print(f"[LEAD] Supabase unavailable, skipping insert: email={email!r}")
    except Exception as exc:
        print(f"[LEAD] DB insert failed: {exc}")

    # Notify ourselves (best-effort)
    try:
        from modules.email_sender import send_lead_notification
        send_lead_notification(email=email, name=name, company=company, message=message, source=source)
    except Exception as exc:
        print(f"[LEAD] Notification email failed: {exc}")

    return jsonify({"status": "received"}), 200
