"""
Health check routes.
"""
from flask import jsonify
from routes import health_bp
from utils.response_helpers import ok


@health_bp.route("/", methods=["GET"])
def root_health():
    """Simple root health check."""
    return ok({
        "status": "healthy",
        "service": "ExecFlex API",
        "message": "Service is running"
    })

