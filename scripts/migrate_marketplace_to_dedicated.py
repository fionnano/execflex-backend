"""One-time migration: copy marketplace rows from the namespaced store
(people_profiles@MARKETPLACE_ORG_ID, opportunities, activity_log) into the
dedicated marketplace_* tables. Idempotent (upsert by id) — safe to re-run.

Run BEFORE deploying the store repoint so the dedicated tables are populated
with zero downtime. Reads/writes via the Supabase service key in .env.

    python scripts/migrate_marketplace_to_dedicated.py          # migrate
    python scripts/migrate_marketplace_to_dedicated.py --dry    # count only
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MKT = "00000000-0000-4000-a000-000000000c0a"
INTRO_ACT = "marketplace_introduction"
COMPANY_ACT = "marketplace_company_profile"


def _seed_company_org_id(company: dict) -> str:
    key = company.get("id") or company.get("name") or "unknown"
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"mkt-seed-company:{key}"))


def _upsert(db, table, row):
    existing = db.table(table).select("id").eq("id", row["id"]).execute().data
    if existing:
        db.table(table).update(row).eq("id", row["id"]).execute()
    else:
        db.table(table).insert(row).execute()


def main(dry=False):
    from config.clients import supabase_client as db

    counts = {"leaders": 0, "companies": 0, "opportunities": 0, "introductions": 0}

    # 1. Leaders: people_profiles@MKT → marketplace_leaders
    leaders = (db.table("people_profiles").select("*")
               .eq("organization_id", MKT).limit(2000).execute().data) or []
    for r in leaders:
        meta = r.get("source_metadata") or {}
        vet = meta.get("vetting") or {}
        name = f"{r.get('first_name') or ''} {r.get('last_name') or ''}".strip() \
            or meta.get("display_name") or "AI Leader"
        row = {
            "id": r["id"],
            "user_id": meta.get("owner_user_id"),
            "name": name,
            "headline": r.get("headline") or meta.get("headline") or "",
            "bio": r.get("bio") or "",
            "location": r.get("location") or "",
            "skills": meta.get("skills") or [],
            "sectors": meta.get("sectors") or [],
            "seniority": meta.get("seniority") or "",
            "track": meta.get("track") or "",
            "engagement": meta.get("engagement") if meta.get("engagement") in ("fractional", "permanent", "both") else "both",
            "comp_expectation": meta.get("comp_expectation") or "",
            "years_experience": r.get("years_experience") or meta.get("years_experience") or 0,
            "contact": meta.get("contact") or {},
            "vetting_status": meta.get("vetting_status") or "pending",
            "vetting": vet or None,
            "vetting_score": vet.get("score"),
            "vetting_attempts": int(meta.get("vetting_attempts") or 0),
        }
        if not dry:
            _upsert(db, "marketplace_leaders", row)
        counts["leaders"] += 1

    # 2. Opportunities: opportunities@MKT (metadata.marketplace) → companies + opps
    opps = (db.table("opportunities").select("*")
            .eq("organization_id", MKT).limit(2000).execute().data) or []
    for r in opps:
        meta = r.get("metadata") or {}
        if not meta.get("marketplace"):
            continue
        company = meta.get("company") or {}
        cid = company.get("id") or str(uuid.uuid5(uuid.NAMESPACE_DNS, "mkt-company-name:" + (company.get("name") or r["id"])))
        crow = {
            "id": cid, "org_id": _seed_company_org_id(company),
            "name": company.get("name") or "", "sector": company.get("sector"),
            "size": company.get("size"), "location": company.get("location"),
            "website": company.get("website"),
        }
        opp_row = {
            "id": r["id"], "company_id": cid, "org_id": MKT,
            "title": r.get("title") or "", "description": r.get("description") or "",
            "track": meta.get("track") or "", "sector": r.get("industry") or company.get("sector") or "",
            "commitment_type": meta.get("engagement") or r.get("commitment_type") or "permanent",
            "location": r.get("location") or "", "is_remote": r.get("is_remote", True),
            "pay_range_min": r.get("pay_range_min"), "pay_range_max": r.get("pay_range_max"),
            "pay_range_currency": r.get("pay_range_currency") or "EUR", "status": "open",
        }
        if not dry:
            _upsert(db, "marketplace_companies", crow)
            _upsert(db, "marketplace_opportunities", opp_row)
        counts["companies"] += 1
        counts["opportunities"] += 1

    # 3. Company profiles: activity_log marketplace_company_profile → marketplace_companies
    comp_rows = (db.table("activity_log").select("*")
                 .eq("activity_type", COMPANY_ACT).limit(2000).execute().data) or []
    for r in comp_rows:
        meta = r.get("metadata") or {}
        if not meta.get("marketplace"):
            continue
        c = meta.get("company") or {}
        crow = {
            "id": c.get("id") or str(uuid.uuid4()),
            "org_id": r.get("organization_id"),
            "name": c.get("name") or "", "sector": c.get("sector"),
            "size": c.get("size"), "location": c.get("location"), "website": c.get("website"),
            "description": c.get("description"), "contact_name": c.get("contact_name"),
            "contact_email": c.get("contact_email"),
        }
        if not dry:
            _upsert(db, "marketplace_companies", crow)
        counts["companies"] += 1

    # 4. Introductions: activity_log marketplace_introduction → marketplace_introductions
    intros = (db.table("activity_log").select("*")
              .eq("activity_type", INTRO_ACT).limit(2000).execute().data) or []
    # Which opportunity ids now exist (FK guard).
    valid_opps = {o["id"] for o in ((db.table("marketplace_opportunities").select("id").limit(5000).execute().data) or [])} \
        if not dry else set()
    for r in intros:
        meta = r.get("metadata") or {}
        if not meta.get("marketplace"):
            continue
        oid = meta.get("opportunity_id")
        row = {
            "id": r["id"], "organization_id": r.get("organization_id"),
            "requested_by": r.get("actor_id"), "leader_id": meta.get("leader_id"),
            "company": meta.get("company") or {},
            "opportunity_id": oid if oid in valid_opps else None,
            "opportunity_title": meta.get("opportunity_title"),
            "status": meta.get("status") or "requested",
            "leader_response": meta.get("leader_response") or "pending",
            "message": meta.get("message") or "",
            "requester_contact": meta.get("requester_contact") or {},
            "leader_contact": meta.get("leader_contact") or {},
            "contact_revealed": bool(meta.get("contact_revealed")),
            "first_year_comp": meta.get("first_year_comp"),
            "placement_fee_pct": meta.get("placement_fee_pct", 15.0),
            "placement_fee_amount": meta.get("placement_fee_amount"),
            "hired": bool(meta.get("hired")), "outcome": meta.get("outcome"),
        }
        if not dry:
            _upsert(db, "marketplace_introductions", row)
        counts["introductions"] += 1

    print(("DRY-RUN would migrate: " if dry else "Migrated: ") + str(counts))
    return counts


if __name__ == "__main__":
    main(dry="--dry" in sys.argv)
