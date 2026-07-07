"""
v1 API routes — org-scoped, multi-tenant, compliance-aware.
All routes require org context via JWT.
"""
from flask import Blueprint

api_v1_bp = Blueprint('api_v1', __name__, url_prefix='/api/v1')

from routes.api_v1 import (  # noqa
    jobs,
    candidates,
    applications,
    screens,
    matches,
    pipeline,
    syndication,
    compliance,
    talent_pools,
    ai,
)

from services.ai.agent_service import AIAgentError
from services.api.responses import api_error


@api_v1_bp.app_errorhandler(AIAgentError)
def _handle_ai_agent_error(e):
    """Temporary diagnostic: when AI_DEBUG_ERRORS=1, agent_service re-raises the
    real failure as AIAgentError and this returns it as a 502 with the actual
    cause (e.g. Anthropic auth/permission/model/quota error) — instead of the
    blind 500. No effect in normal operation (the flag is off, nothing raises)."""
    return api_error(f"AI agent failed: {e}", 502)
