"""
Rate limiting utilities for Flask endpoints.
"""
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Global limiter instance (will be initialized in server.py)
limiter = None


def create_limiter(app):
    """
    Create and configure Flask-Limiter instance.
    
    Args:
        app: Flask application instance
        
    Returns:
        Limiter instance
    """
    global limiter
    limiter = Limiter(
        app=app,
        key_func=get_remote_address,
        default_limits=["200 per day", "50 per hour"],
        storage_uri="memory://",  # In-memory storage (use Redis in production for better scalability)
        headers_enabled=True  # Include rate limit headers in responses
    )
    return limiter


def get_limiter():
    """Get the global limiter instance."""
    return limiter

