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
)
