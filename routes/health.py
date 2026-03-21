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


@health_bp.route("/submit-brief", methods=["POST"])
def submit_brief():
    """Capture landing page lead submissions. Best-effort — no auth required."""
    try:
        from flask import request as req
        data = req.get_json(silent=True) or {}
        name = str(data.get("name", ""))[:200]
        email = str(data.get("email", ""))[:200]
        source = str(data.get("source", "landing"))[:100]
        # Log it so we can see submissions in Render logs
        print(f"[LEAD] name={name!r} email={email!r} source={source!r}")
        # TODO: store in Supabase or send email notification
    except Exception as exc:
        print(f"[LEAD] error: {exc}")
    return ok({"received": True})
