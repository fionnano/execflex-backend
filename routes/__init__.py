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
onboarding_bp = Blueprint('onboarding', __name__, url_prefix='/onboarding')
screening_bp = Blueprint('screening', __name__, url_prefix='/screening')

# Import route handlers to register them
from routes import health, matching, roles, introductions, voice, onboarding, screening  # noqa

# Cara voice session blueprint (created in cara_voice.py)
from routes.cara_voice import cara_bp  # noqa

# Cara outbound voice calls (onboarding, reference, exit interview)
from routes.voice_calls import voice_calls_bp  # noqa

