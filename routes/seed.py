"""
Demo data seeder for the Moorepark CFO meeting.

  POST   /admin/seed-demo   — create a full end-to-end demo: opportunity,
                              organisation, 3 candidates, 3 completed
                              screening calls with realistic scores,
                              and 3 bias-audit rows.
  DELETE /admin/seed-demo   — remove every row the seeder created.

Everything the seeder writes is tagged so the DELETE endpoint can
find it cleanly and nothing production gets touched:

  people_profiles        source = 'demo'
  opportunities          metadata->>'is_demo' = 'true'
  interactions           artifacts->>'is_demo' = 'true'
  outbound_call_jobs     artifacts->>'is_demo' = 'true'
  organizations          name = 'Moorepark Technology'  +  metadata.is_demo
  screening_bias_audit   role_title = 'Chief Financial Officer' AND
                         company_name = 'Moorepark Technology'

The demo is deliberately NOT idempotent — running POST twice produces
two sets. Always DELETE first if re-seeding.
"""
from datetime import datetime, timezone
from flask import Blueprint, request

from utils.auth_helpers import require_admin
from utils.response_helpers import ok, bad
from config.clients import supabase_client


seed_bp = Blueprint("seed", __name__)


_DEMO_COMPANY = "Moorepark Technology"
_DEMO_ROLE_TITLE = "Chief Financial Officer"
_DEMO_INDUSTRY = "AgriTech"
_DEMO_LOCATION = "Cork, Ireland"
_DEMO_COMPENSATION = "€120,000 - €150,000"
_DEMO_COMMITMENT = "full_time"
_DEMO_DESCRIPTION = (
    "We are seeking an experienced CFO to join our leadership team as "
    "we scale our AgriTech platform across European markets. The "
    "successful candidate will have strong experience in SaaS finance, "
    "fundraising, and board reporting."
)


# ── Demo candidate profiles ──────────────────────────────────────────────────
#
# Names use the PDL-style first-initial obfuscation to match the look
# of real sourced candidates in the product UI.

_DEMO_CANDIDATES = [
    {
        "first_name": "Sarah",
        "last_name": "M.",
        "headline": "Chief Financial Officer at Irish FinTech Scale-up",
        "location": "Dublin, Ireland",
        "years_experience": 14,
        "recommendation": "strong_proceed",
        "screening_scores": [
            {"competency": "Financial Leadership", "score": 4.8, "weight": 1},
            {"competency": "Board Reporting",       "score": 4.5, "weight": 1},
            {"competency": "Fundraising Experience","score": 4.2, "weight": 1},
            {"competency": "Team Leadership",       "score": 4.6, "weight": 1},
            {"competency": "Strategic Thinking",    "score": 4.7, "weight": 1},
        ],
        "transcript_text": (
            "AIDAN: Tell me about the largest P&L you have managed?\n"
            "CANDIDATE: I managed a €45M P&L at my previous role, "
            "overseeing finance across three European entities with a "
            "team of 12. We delivered 22% YoY revenue growth while "
            "tightening the cash runway by six months, and I personally "
            "chaired the quarterly board-finance review.\n"
            "AIDAN: What's your experience with board-level financial reporting?\n"
            "CANDIDATE: I presented at every board meeting for four "
            "years — monthly management accounts, quarterly deep dives, "
            "and the annual plan. I introduced a one-page finance "
            "summary that the chair now uses across the portfolio.\n"
            "AIDAN: Have you led a fundraise or M&A process?\n"
            "CANDIDATE: Yes — I led a €15M Series B from the data room "
            "through to closing, and I ran the sell-side on a bolt-on "
            "acquisition last year. I worked directly with the lead "
            "investor's operating partner throughout both processes.\n"
            "AIDAN: What size finance team have you led?\n"
            "CANDIDATE: Twelve people at peak — two accounting, two FP&A, "
            "two treasury, one tax, two ops finance, and three business "
            "partners. I'm a big believer in having finance business "
            "partners embedded in each commercial function.\n"
            "AIDAN: What sectors have you worked in most deeply?\n"
            "CANDIDATE: FinTech and SaaS primarily — I find the recurring-"
            "revenue motion genuinely interesting because the finance "
            "function has to be forward-looking rather than just a "
            "reporting layer."
        ),
    },
    {
        "first_name": "James",
        "last_name": "O.",
        "headline": "VP Finance at European SaaS Company",
        "location": "Cork, Ireland",
        "years_experience": 11,
        "recommendation": "proceed",
        "screening_scores": [
            {"competency": "Financial Leadership", "score": 4.1, "weight": 1},
            {"competency": "Board Reporting",       "score": 3.8, "weight": 1},
            {"competency": "Fundraising Experience","score": 4.0, "weight": 1},
            {"competency": "Team Leadership",       "score": 4.2, "weight": 1},
            {"competency": "Strategic Thinking",    "score": 3.9, "weight": 1},
        ],
        "transcript_text": (
            "AIDAN: Tell me about the largest P&L you have managed?\n"
            "CANDIDATE: As VP Finance I had dotted-line ownership of a "
            "€28M ARR business — I was accountable for gross margin, "
            "opex, and cash, reporting into the CFO. We moved the "
            "business from 62% to 71% gross margin over two years.\n"
            "AIDAN: What's your experience with board-level financial reporting?\n"
            "CANDIDATE: I prepared the board pack for every meeting and "
            "presented sections myself — mainly the revenue waterfall and "
            "the cash-flow forecast. I always prepared with the CFO the "
            "day before so we were aligned on the narrative.\n"
            "AIDAN: Have you led a fundraise or M&A process?\n"
            "CANDIDATE: I was deeply involved in our Series C — I built "
            "the financial model, ran the data room, and handled due "
            "diligence questions. The CFO led the investor meetings but "
            "I was in the room for all of them.\n"
            "AIDAN: What size finance team have you led?\n"
            "CANDIDATE: Six — three FP&A analysts and three accountants. "
            "I coached all of them through the Series C process and two "
            "were promoted as a direct result.\n"
            "AIDAN: What sectors have you worked in most deeply?\n"
            "CANDIDATE: Vertical SaaS for the last seven years — before "
            "that, manufacturing finance which gave me a really good "
            "grounding in cost accounting."
        ),
    },
    {
        "first_name": "Aoife",
        "last_name": "B.",
        "headline": "Finance Director at AgriTech Company",
        "location": "Limerick, Ireland",
        "years_experience": 9,
        "recommendation": "proceed",
        "screening_scores": [
            {"competency": "Financial Leadership", "score": 3.9, "weight": 1},
            {"competency": "Board Reporting",       "score": 3.6, "weight": 1},
            {"competency": "Fundraising Experience","score": 3.5, "weight": 1},
            {"competency": "Team Leadership",       "score": 3.8, "weight": 1},
            {"competency": "Strategic Thinking",    "score": 3.7, "weight": 1},
        ],
        "transcript_text": (
            "AIDAN: Tell me about the largest P&L you have managed?\n"
            "CANDIDATE: As Finance Director I've had full P&L ownership "
            "for an €18M AgriTech business — two production sites, a "
            "direct-to-farm sales channel, and a distributor network "
            "across Ireland and the UK.\n"
            "AIDAN: What's your experience with board-level financial reporting?\n"
            "CANDIDATE: I present at every monthly board meeting and I "
            "own the annual audit relationship. Our board is quite "
            "hands-on so I get a lot of questions on unit economics and "
            "working capital specifically.\n"
            "AIDAN: Have you led a fundraise or M&A process?\n"
            "CANDIDATE: I supported our last fundraise — a €6M growth "
            "round — by preparing the data room and the financial "
            "projections, but I wasn't the lead. I'd love to be the "
            "lead on the next one.\n"
            "AIDAN: What size finance team have you led?\n"
            "CANDIDATE: Four direct reports plus two shared-services "
            "accountants. Small team but we cover everything — "
            "management accounts, statutory, tax, and commercial finance.\n"
            "AIDAN: What sectors have you worked in most deeply?\n"
            "CANDIDATE: AgriTech is where I've spent the last four years "
            "— I genuinely love it because the finance challenges are so "
            "tangible. Before that, retail finance which taught me a lot "
            "about inventory and gross margin."
        ),
    },
]


def _overall_score(scores: list) -> float:
    if not scores:
        return 0.0
    weighted = sum((s.get("score") or 0) * (s.get("weight") or 1) for s in scores)
    total_w = sum((s.get("weight") or 1) for s in scores) or 1
    return round(weighted / total_w, 2)


@seed_bp.route("/admin/seed-demo", methods=["POST"])
@require_admin
def seed_demo():
    """
    Create the Moorepark demo pack: opportunity + 3 candidates + 3
    screening interactions + 3 bias audit rows.

    Returns {opportunity_id, candidate_ids, message}.
    """
    if not supabase_client:
        return bad("Database not available", 503)

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    user_id = request.environ.get("authenticated_user_id")

    candidate_ids: list = []
    interaction_ids: list = []
    job_ids: list = []

    # ── 1. Organisation ─────────────────────────────────────────────────
    org_id = None
    try:
        org_resp = (
            supabase_client.table("organizations")
            .select("id")
            .eq("name", _DEMO_COMPANY)
            .limit(1)
            .execute()
        )
        if org_resp.data:
            org_id = org_resp.data[0]["id"]
            print(f"[SEED] Reusing organization {org_id} ({_DEMO_COMPANY})", flush=True)
        else:
            ins = (
                supabase_client.table("organizations")
                .insert({
                    "name": _DEMO_COMPANY,
                    "industry": _DEMO_INDUSTRY,
                    "location": _DEMO_LOCATION,
                    "metadata": {"is_demo": True},
                })
                .execute()
            )
            if ins.data:
                org_id = ins.data[0]["id"]
                print(f"[SEED] Created organization {org_id}", flush=True)
    except Exception as e:
        print(f"[SEED] organization insert failed: {e}", flush=True)

    # ── 2. Opportunity ──────────────────────────────────────────────────
    opp_id = None
    try:
        opp_payload = {
            "created_by_user_id": user_id,
            "organization_id": org_id,
            "type": "hire_fractional",
            "title": _DEMO_ROLE_TITLE,
            "description": _DEMO_DESCRIPTION,
            "industry": _DEMO_INDUSTRY,
            "location": _DEMO_LOCATION,
            "is_remote": False,
            "commitment_type": _DEMO_COMMITMENT,
            "compensation": _DEMO_COMPENSATION,
            "status": "open",
            "metadata": {
                "is_demo": True,
                "experience_level": "senior",
                "role_type": "executive",
                "created_for": "moorepark_meeting",
            },
        }
        opp_resp = supabase_client.table("opportunities").insert(opp_payload).execute()
        if opp_resp.data:
            opp_id = opp_resp.data[0]["id"]
            print(f"[SEED] Created opportunity {opp_id}", flush=True)
    except Exception as e:
        print(f"[SEED] opportunity insert failed: {e}", flush=True)
        return bad(f"Failed to create demo opportunity: {e}", 500)

    # ── 3. Candidates + interactions + jobs + bias audit ────────────────
    for cand in _DEMO_CANDIDATES:
        profile_id = None
        try:
            prof_ins = (
                supabase_client.table("people_profiles")
                .insert({
                    "first_name": cand["first_name"],
                    "last_name": cand["last_name"],
                    "headline": cand["headline"],
                    "location": cand["location"],
                    "years_experience": cand["years_experience"],
                    "approved": True,
                    "source": "demo",
                    "source_metadata": {
                        "is_demo": True,
                        "demo_role": _DEMO_ROLE_TITLE,
                        "demo_opportunity_id": opp_id,
                        "seeded_at": now_iso,
                    },
                })
                .execute()
            )
            if prof_ins.data:
                profile_id = prof_ins.data[0]["id"]
                candidate_ids.append(profile_id)
        except Exception as e:
            print(f"[SEED] profile insert failed for {cand['first_name']}: {e}", flush=True)
            continue

        if not profile_id:
            continue

        overall = _overall_score(cand["screening_scores"])
        candidate_full_name = f"{cand['first_name']} {cand['last_name']}"

        # Interaction with transcript + scores
        interaction_id = None
        try:
            ix_ins = (
                supabase_client.table("interactions")
                .insert({
                    "channel": "voice",
                    "direction": "outbound",
                    "provider": "twilio",
                    "started_at": now_iso,
                    "ended_at": now_iso,
                    "transcript_text": cand["transcript_text"],
                    "screening_scores": cand["screening_scores"],
                    "screening_recommendation": cand["recommendation"],
                    "summary_text": (
                        f"Screening call with {candidate_full_name} for "
                        f"{_DEMO_ROLE_TITLE} at {_DEMO_COMPANY}. Overall "
                        f"score {overall}/5.0, recommendation "
                        f"{cand['recommendation']}."
                    ),
                    "artifacts": {
                        "is_demo": True,
                        "demo_candidate": candidate_full_name,
                        "seeded_at": now_iso,
                    },
                })
                .execute()
            )
            if ix_ins.data:
                interaction_id = ix_ins.data[0]["id"]
                interaction_ids.append(interaction_id)
        except Exception as e:
            print(f"[SEED] interaction insert failed for {candidate_full_name}: {e}", flush=True)

        # Outbound call job — dashboards/shortlist pages find screenings
        # via outbound_call_jobs.artifacts.screening_context
        job_id = None
        try:
            job_ins = (
                supabase_client.table("outbound_call_jobs")
                .insert({
                    "phone_e164": "+353000000000",  # fake — never dialled
                    "status": "completed",
                    "interaction_id": interaction_id,
                    "created_at": now_iso,
                    "updated_at": now_iso,
                    "artifacts": {
                        "is_demo": True,
                        "call_type": "screening",
                        "call_status": "completed",
                        "call_duration": 420,  # 7 minutes
                        "seeded_at": now_iso,
                        "screening_context": {
                            "candidate_name": candidate_full_name,
                            "role_title": _DEMO_ROLE_TITLE,
                            "company_name": _DEMO_COMPANY,
                            "source_candidate_id": profile_id,
                            "role_id": opp_id,
                            "consent_requested": True,
                        },
                    },
                })
                .execute()
            )
            if job_ins.data:
                job_id = job_ins.data[0]["id"]
                job_ids.append(job_id)
        except Exception as e:
            print(f"[SEED] job insert failed for {candidate_full_name}: {e}", flush=True)

        # Bias audit row — lets GET /screening/bias-audit/<role_id> show
        # a Low risk rating for the demo role.
        if interaction_id:
            try:
                supabase_client.table("screening_bias_audit").insert({
                    "interaction_id": interaction_id,
                    "job_id": job_id,
                    "role_id": opp_id,
                    "role_title": _DEMO_ROLE_TITLE,
                    "company_name": _DEMO_COMPANY,
                    "questions_asked": 5,
                    "questions_expected": 5,
                    "questions_skipped": 0,
                    "question_order_preserved": True,
                    "overall_score": overall,
                    "recommendation": cand["recommendation"],
                    "score_std_deviation": 0.25,
                    "bias_flags": [],
                    "ai_disclosure_given": True,
                    "candidate_consented": True,
                    "scoring_model": "gpt-4o",
                    "prompt_version": "v2_ainm_cadence",
                    "transcript_length": len(cand["transcript_text"]),
                    "call_duration_seconds": 420,
                }).execute()
            except Exception as e:
                print(f"[SEED] bias audit insert failed: {e}", flush=True)

    print(
        f"[SEED] Demo created: org={org_id} opp={opp_id} "
        f"candidates={len(candidate_ids)} interactions={len(interaction_ids)} "
        f"jobs={len(job_ids)}",
        flush=True,
    )

    return ok({
        "opportunity_id": opp_id,
        "organization_id": org_id,
        "candidate_ids": candidate_ids,
        "interaction_ids": interaction_ids,
        "job_ids": job_ids,
        "message": "Demo data created. Visit /dashboard to see it.",
    }, status=201)


@seed_bp.route("/admin/seed-demo", methods=["DELETE"])
@require_admin
def unseed_demo():
    """
    Delete every row the seeder created. Matches on the markers written
    by POST /admin/seed-demo:
      - people_profiles.source = 'demo'
      - opportunities.metadata->>'is_demo' = 'true'
      - interactions.artifacts->>'is_demo' = 'true'
      - outbound_call_jobs.artifacts->>'is_demo' = 'true'
      - organizations.name = _DEMO_COMPANY AND metadata->>'is_demo' = 'true'
      - screening_bias_audit by role_title + company_name
    """
    if not supabase_client:
        return bad("Database not available", 503)

    counts: dict = {}

    def _delete(label: str, fn):
        try:
            resp = fn()
            n = len(resp.data or []) if hasattr(resp, "data") else 0
            counts[label] = n
        except Exception as e:
            print(f"[SEED] delete {label} failed: {e}", flush=True)
            counts[label] = f"error: {e}"

    # Order matters for FKs: bias_audit -> jobs -> interactions -> candidates
    # -> opportunities -> organization
    _delete(
        "bias_audit",
        lambda: supabase_client.table("screening_bias_audit")
        .delete()
        .eq("role_title", _DEMO_ROLE_TITLE)
        .eq("company_name", _DEMO_COMPANY)
        .execute(),
    )
    _delete(
        "outbound_call_jobs",
        lambda: supabase_client.table("outbound_call_jobs")
        .delete()
        .eq("artifacts->>is_demo", "true")
        .execute(),
    )
    _delete(
        "interactions",
        lambda: supabase_client.table("interactions")
        .delete()
        .eq("artifacts->>is_demo", "true")
        .execute(),
    )
    _delete(
        "people_profiles",
        lambda: supabase_client.table("people_profiles")
        .delete()
        .eq("source", "demo")
        .execute(),
    )
    _delete(
        "opportunities",
        lambda: supabase_client.table("opportunities")
        .delete()
        .eq("metadata->>is_demo", "true")
        .execute(),
    )
    _delete(
        "organizations",
        lambda: supabase_client.table("organizations")
        .delete()
        .eq("name", _DEMO_COMPANY)
        .eq("metadata->>is_demo", "true")
        .execute(),
    )

    print(f"[SEED] Demo deleted: {counts}", flush=True)
    return ok({"deleted": counts, "message": "Demo data removed."}, status=200)
