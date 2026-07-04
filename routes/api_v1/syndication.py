"""Job syndication endpoints — org-scoped."""
from flask import request
from routes.api_v1 import api_v1_bp
from services.api.auth import require_org, get_org_context
from services.api.responses import api_ok, api_error


@api_v1_bp.route('/syndication/boards', methods=['GET'])
@require_org()
def list_boards():
    from services.syndication.engine import SyndicationEngine
    engine = SyndicationEngine()
    return api_ok({"boards": engine.available_boards})


@api_v1_bp.route('/syndication/submit', methods=['POST'])
@require_org(allowed_roles=["owner", "recruiter"])
def syndicate_job():
    ctx = get_org_context()
    data = request.get_json()
    if not data:
        return api_error("Request body required", 400)

    job_id = data.get("job_id")
    boards = data.get("boards", [])
    if not job_id:
        return api_error("job_id required", 400)
    if not boards:
        return api_error("boards array required", 400)

    from config.clients import supabase_client
    job_result = supabase_client.table("opportunities") \
        .select("*") \
        .eq("id", job_id) \
        .eq("organization_id", ctx.org_id) \
        .execute()
    if not job_result.data:
        return api_error("Job not found", 404)

    j = job_result.data[0]
    org_result = supabase_client.table("organizations") \
        .select("name") \
        .eq("id", ctx.org_id) \
        .execute()
    company_name = org_result.data[0]["name"] if org_result.data else "ExecFlex Client"

    from services.syndication.adapters import JobPosting
    from services.syndication.engine import SyndicationEngine

    posting = JobPosting(
        id=j["id"],
        title=j.get("title", ""),
        description=j.get("description", ""),
        location=j.get("location", ""),
        company_name=company_name,
        pay_range_min=j.get("pay_range_min") or 0,
        pay_range_max=j.get("pay_range_max") or 0,
        pay_currency=j.get("pay_range_currency", "EUR"),
        pay_period=j.get("pay_range_period", "annual"),
        commitment_type=j.get("commitment_type", ""),
        industry=j.get("industry", ""),
        is_remote=j.get("is_remote", False),
        posted_at=j.get("created_at", ""),
    )

    engine = SyndicationEngine()
    results = engine.syndicate(posting, boards)

    for r in results:
        supabase_client.table("job_syndication").insert({
            "organization_id": ctx.org_id,
            "opportunity_id": job_id,
            "board": r.board,
            "external_id": r.external_id,
            "status": "live" if r.success else "failed",
            "error_message": r.error,
            "metadata": {"feed_xml_length": len(r.feed_xml) if r.feed_xml else 0},
        }).execute()

    from services.compliance.decision_logger import log_activity
    board_names = ", ".join(boards)
    log_activity(ctx.org_id, "job", job_id, "job_syndicated",
                 ctx.user_id, f"Syndicated to: {board_names}")

    return api_ok({
        "job_id": job_id,
        "results": [
            {"board": r.board, "success": r.success, "external_id": r.external_id, "error": r.error}
            for r in results
        ],
    })


@api_v1_bp.route('/syndication/status/<job_id>', methods=['GET'])
@require_org()
def syndication_status(job_id):
    ctx = get_org_context()
    from config.clients import supabase_client
    result = supabase_client.table("job_syndication") \
        .select("*") \
        .eq("organization_id", ctx.org_id) \
        .eq("opportunity_id", job_id) \
        .order("created_at", desc=True) \
        .execute()
    return api_ok(result.data)


@api_v1_bp.route('/syndication/feed/<board>', methods=['GET'])
def get_feed(board):
    """Public XML feed endpoint for a board. No auth required — boards pull this."""
    org_id = request.args.get('org_id')
    if not org_id:
        return api_error("org_id query param required", 400)

    from config.clients import supabase_client
    jobs_result = supabase_client.table("opportunities") \
        .select("*") \
        .eq("organization_id", org_id) \
        .eq("status", "open") \
        .execute()

    from services.syndication.adapters import JobPosting
    from services.syndication.engine import SyndicationEngine

    postings = [
        JobPosting(
            id=j["id"], title=j.get("title", ""), description=j.get("description", ""),
            location=j.get("location", ""), company_name="ExecFlex",
            pay_range_min=j.get("pay_range_min") or 0, pay_range_max=j.get("pay_range_max") or 0,
            commitment_type=j.get("commitment_type", ""),
        )
        for j in jobs_result.data
    ]

    engine = SyndicationEngine()
    feed = engine.generate_feed(board, postings)
    if feed is None:
        return api_error(f"Unknown board: {board}", 404)

    from flask import Response
    return Response(feed, mimetype='application/xml')
