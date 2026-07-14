"""Idempotent marketplace seeder.

Writes the synthetic pool (leaders, opportunities, introductions) using
deterministic ids so running it twice converges to the same state. Verified
leaders get a synthetic-but-explainable vetting record (no LLM call at seed
time — the rationale is composed deterministically so seeding is fast and
token-free).
"""
from __future__ import annotations

from services.marketplace import store
from services.marketplace.constants import VETTING_PASS_THRESHOLD
from services.marketplace.seed_data import (
    COMPANIES, LEADERS, OPPORTUNITIES, INTRODUCTIONS,
)
from services.marketplace.vetting import question_set


def _synthetic_vetting(leader: dict) -> dict:
    """Compose a deterministic vetting record for a seeded verified leader."""
    score = leader["score"]
    qs = question_set(leader["track"])
    # Spread per-competency scores around the overall, clamped 0-100.
    per = []
    for i, q in enumerate(qs):
        delta = (i % 3 - 1) * 4  # -4, 0, +4 pattern
        s = max(40, min(100, score + delta))
        per.append({"competency": q["competency"], "score": s, "note": "seed assessment"})
    name = leader["name"]
    top = max(per, key=lambda p: p["score"])
    low = min(per, key=lambda p: p["score"])
    rationale = (
        f"{name} scored {score}/100 on the independent {leader['track'].replace('_', ' ')} "
        f"assessment, clearing the {VETTING_PASS_THRESHOLD} bar. Strongest in "
        f"{top['competency']} ({top['score']}/100); most room in {low['competency']} "
        f"({low['score']}/100). Assessed on specificity, quantified evidence, and "
        f"ownership across a fixed technical + leadership question set."
    )
    return {
        "score": score,
        "passed": True,
        "status": "verified",
        "rationale": rationale,
        "per_competency": per,
        "model_used": "marketplace_vetting_v1 (seed)",
        "ai_generated": False,
        "confidence": "medium",
        "flags": [],
        "threshold": VETTING_PASS_THRESHOLD,
        "track": leader["track"],
    }


def seed(purge_first: bool = True) -> dict:
    """Seed the full synthetic marketplace pool. Idempotent."""
    result = {"purged": None, "leaders": 0, "opportunities": 0, "introductions": 0}
    store.ensure_marketplace_org()
    if purge_first:
        result["purged"] = store.purge_marketplace()

    # Leaders
    leader_by_key = {}
    for ld in LEADERS:
        status = ld.get("status", "verified")
        vetting = _synthetic_vetting(ld) if status == "verified" and ld.get("score") else {}
        created = store.create_leader(
            leader_id=ld["id"], name=ld["name"], headline=ld["headline"],
            location=ld["location"], skills=ld["skills"], sectors=ld["sectors"],
            seniority=ld["seniority"], track=ld["track"], engagement=ld["engagement"],
            comp_expectation=ld["comp"], years_experience=ld["years"],
            vetting=vetting, vetting_status=status,
        )
        leader_by_key[ld["key"]] = ld
        result["leaders"] += 1

    # Opportunities
    for op in OPPORTUNITIES:
        store.create_opportunity(
            opp_id=op["id"], title=op["title"], company=op["company"],
            description=op["desc"], location=op["location"],
            commitment_type=op["commitment"], is_remote=op["remote"],
            sector=op["sector"], track=op["track"],
            pay_range_min=op["min"], pay_range_max=op["max"],
        )
        result["opportunities"] += 1

    # Introductions (owned by the marketplace org in the seed)
    from services.marketplace.constants import MARKETPLACE_ORG_ID
    opp_by_key = {op["key"]: op for op in OPPORTUNITIES}
    comp_by_name = {c["name"]: c for c in COMPANIES}
    for intro in INTRODUCTIONS:
        ld = leader_by_key[intro["leader"]]
        opp = opp_by_key[intro["opp"]]
        store.create_introduction(
            intro_id=intro["id"], org_id=MARKETPLACE_ORG_ID, actor_id=MARKETPLACE_ORG_ID,
            leader_id=ld["id"], leader_name=ld["name"],
            company=comp_by_name[intro["company"]],
            opportunity_id=opp["id"], opportunity_title=opp["title"],
            message=intro["message"], first_year_comp=intro["first_year_comp"],
            fee_pct=intro["fee_pct"], status=intro["status"],
            hired=(intro["status"] == "hired"),
        )
        result["introductions"] += 1

    return result
