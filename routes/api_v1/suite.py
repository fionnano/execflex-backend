"""ainm suite API — the unifying shell layer (org-scoped).

Returns the modules a logged-in user/org is entitled to, for the suite home /
module switcher. Does NOT change any product's internals.

Routes (under /api/v1/suite):
  GET /suite/modules        entitled modules for the caller (module switcher)
  GET /suite/modules?all=1  full registry with an `entitled` flag (upsell view)
"""
from flask import request

from routes.api_v1 import api_v1_bp
from services.api.auth import require_org, get_org_context
from services.api.responses import api_ok
from services.suite import modules as M


@api_v1_bp.route('/suite/modules', methods=['GET'])
@require_org()
def suite_modules():
    ctx = get_org_context()
    include_locked = request.args.get("all") in ("1", "true", "yes")
    mods = M.resolve_modules(ctx.org_id, include_locked=include_locked)
    return api_ok({
        "modules": mods,
        "total": len(mods),
        "user_id": ctx.user_id,
        "org_id": ctx.org_id,
        "role": ctx.role,
    })
