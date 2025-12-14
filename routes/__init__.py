"""
Route blueprints package.
"""
from flask import Blueprint

# Create blueprints
health_bp = Blueprint('health', __name__)
matching_bp = Blueprint('matching', __name__)
roles_bp = Blueprint('roles', __name__)
introductions_bp = Blueprint('introductions', __name__)
voice_bp = Blueprint('voice', __name__, url_prefix='/voice')
qualification_bp = Blueprint('qualification', __name__, url_prefix='/voice')

# Import route handlers to register them
from routes import health, matching, roles, introductions, voice, qualification  # noqa

