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
