"""
Health check routes.
"""
from routes import health_bp


@health_bp.route("/", methods=["GET"])
def root_health():
    """Simple root health check."""
    return "", 200

