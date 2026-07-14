"""Marketplace persistence — maps marketplace entities onto existing durable tables.

See DECISIONS.md D-14. No new prod DDL is required: leaders live in
people_profiles (namespaced by MARKETPLACE_ORG_ID + source='marketplace_leader'),
companies+roles in opportunities (metadata.marketplace), and the billable
introductions in activity_log (entity_type='placement', metadata.marketplace).

Every function is defensive: the Supabase client is fetched lazily so imports
never fail, and reads tolerate missing/legacy rows.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from services.marketplace.constants import (
    MARKETPLACE_ORG_ID,
    MARKETPLACE_ORG_NAME,
    LEADER_SOURCE,
    INTRO_ENTITY_TYPE,
    DEFAULT_PLACEMENT_FEE_PCT,
)


def _db():
    from config.clients import supabase_client
    return supabase_client


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_system_actor_cache: Optional[str] = None


def _system_actor() -> Optional[str]:
    """A valid users.id to satisfy NOT-NULL FK columns (opportunities.created_by_user_id).

    Marketplace catalog rows have no human owner, so reuse any existing user id
    as the system actor. Discovered once from an existing opportunity and cached.
    """
    global _system_actor_cache
    if _system_actor_cache:
        return _system_actor_cache
    db = _db()
    try:
        rows = db.table("opportunities").select("created_by_user_id").limit(1).execute().data
        if rows and rows[0].get("created_by_user_id"):
            _system_actor_cache = rows[0]["created_by_user_id"]
            return _system_actor_cache
    except Exception:
        pass
    try:
        rows = db.table("people_profiles").select("user_id").not_.is_("user_id", "null").limit(1).execute().data
        if rows and rows[0].get("user_id"):
            _system_actor_cache = rows[0]["user_id"]
    except Exception:
        pass
    return _system_actor_cache


# ── Marketplace org (namespace anchor) ───────────────────────────────────────

def ensure_marketplace_org() -> str:
    """Ensure the single dedicated marketplace org row exists. Idempotent."""
    db = _db()
    existing = db.table("organizations").select("id").eq("id", MARKETPLACE_ORG_ID).execute()
    if not existing.data:
        db.table("organizations").insert({
            "id": MARKETPLACE_ORG_ID,
            "name": MARKETPLACE_ORG_NAME,
            "industry": "Marketplace",
            "location": "Remote / EU",
        }).execute()
    return MARKETPLACE_ORG_ID


# ── Leaders (people_profiles) ────────────────────────────────────────────────

def _leader_from_row(row: dict) -> dict:
    """Serialize a people_profiles row into the marketplace leader shape."""
    meta = row.get("source_metadata") or {}
    vet = meta.get("vetting") or {}
    first = row.get("first_name") or ""
    last = row.get("last_name") or ""
    name = f"{first} {last}".strip() or meta.get("display_name") or "AI Leader"
    return {
        "id": row.get("id"),
        "name": name,
        "headline": row.get("headline") or meta.get("headline") or "",
        "bio": row.get("bio") or "",
        "location": row.get("location") or "",
        "skills": row.get("skills") or meta.get("skills") or [],
        "sectors": row.get("industries") or meta.get("sectors") or [],
        "seniority": meta.get("seniority") or "",
        "track": meta.get("track") or "",
        "engagement": meta.get("engagement") or row.get("availability_type") or "both",
        "comp_expectation": meta.get("comp_expectation") or row.get("rate_range") or "",
        "years_experience": row.get("years_experience") or meta.get("years_experience") or 0,
        "vetting_status": meta.get("vetting_status") or "pending",
        "vetting_score": vet.get("score"),
        "vetting": vet or None,
        "avatar_initials": "".join(p[0] for p in name.split()[:2]).upper() if name else "AI",
        "created_at": row.get("created_at"),
    }


def list_leaders(*, status: Optional[str] = "verified", skill: Optional[str] = None,
                 seniority: Optional[str] = None, engagement: Optional[str] = None,
                 sector: Optional[str] = None, track: Optional[str] = None,
                 limit: int = 200) -> list[dict]:
    """List curated leaders (global catalog read — intentionally not org-scoped).

    Filters are applied in Python against the JSONB-derived shape so the demo
    can filter on marketplace-specific fields the base table doesn't index.
    """
    db = _db()
    rows = (db.table("people_profiles")
            .select("*")
            .eq("organization_id", MARKETPLACE_ORG_ID)
            .order("created_at", desc=True)
            .limit(limit).execute().data) or []
    leaders = [_leader_from_row(r) for r in rows]

    def keep(ld: dict) -> bool:
        if status and ld["vetting_status"] != status:
            return False
        if skill and not any(skill.lower() in (s or "").lower() for s in ld["skills"]):
            return False
        if seniority and seniority.lower() not in (ld["seniority"] or "").lower():
            return False
        if engagement and engagement != "both":
            e = ld["engagement"]
            if e not in (engagement, "both"):
                return False
        if sector and not any(sector.lower() in (s or "").lower() for s in ld["sectors"]):
            return False
        if track and ld["track"] != track:
            return False
        return True

    return [ld for ld in leaders if keep(ld)]


def get_leader(leader_id: str) -> Optional[dict]:
    db = _db()
    rows = (db.table("people_profiles").select("*")
            .eq("id", leader_id).eq("organization_id", MARKETPLACE_ORG_ID).execute().data)
    return _leader_from_row(rows[0]) if rows else None


def create_leader(*, name: str, headline: str, bio: str = "", location: str = "",
                  skills: Optional[list] = None, sectors: Optional[list] = None,
                  seniority: str = "", track: str = "", engagement: str = "both",
                  comp_expectation: str = "", years_experience: int = 0,
                  leader_id: Optional[str] = None, vetting: Optional[dict] = None,
                  vetting_status: str = "pending") -> dict:
    """Create (or upsert by id) a marketplace leader in people_profiles."""
    ensure_marketplace_org()
    db = _db()
    parts = name.split()
    first = parts[0] if parts else name
    last = " ".join(parts[1:]) if len(parts) > 1 else ""
    row_id = leader_id or str(uuid.uuid4())
    source_metadata = {
        "marketplace": True,
        "seniority": seniority,
        "track": track,
        "engagement": engagement,
        "comp_expectation": comp_expectation,
        "sectors": sectors or [],
        "years_experience": years_experience,
        "vetting_status": vetting_status,
        "vetting": vetting or {},
        "display_name": name,
        "headline": headline,
    }
    # NOTE: industries / availability_type / skills are constrained columns
    # (enum arrays) on people_profiles. All marketplace-specific list/typed data
    # lives in source_metadata (JSONB, unconstrained); the marketplace namespace
    # marker is organization_id = MARKETPLACE_ORG_ID (used for every read/purge).
    source_metadata["skills"] = skills or []
    row = {
        "id": row_id,
        "organization_id": MARKETPLACE_ORG_ID,
        "first_name": first,
        "last_name": last,
        "headline": headline,
        "bio": bio,
        "location": location,
        "years_experience": years_experience,
        "source_metadata": source_metadata,
    }
    existing = db.table("people_profiles").select("id").eq("id", row_id).execute().data
    if existing:
        db.table("people_profiles").update(row).eq("id", row_id).execute()
    else:
        db.table("people_profiles").insert(row).execute()
    return _leader_from_row(row)


def set_leader_vetting(leader_id: str, vetting: dict, status: str) -> Optional[dict]:
    """Persist a vetting result onto a leader and update vetting_status."""
    db = _db()
    rows = (db.table("people_profiles").select("source_metadata")
            .eq("id", leader_id).eq("organization_id", MARKETPLACE_ORG_ID).execute().data)
    if not rows:
        return None
    meta = rows[0].get("source_metadata") or {}
    meta["vetting"] = vetting
    meta["vetting_status"] = status
    db.table("people_profiles").update({"source_metadata": meta}).eq("id", leader_id).execute()
    return get_leader(leader_id)


# ── Opportunities + companies (opportunities) ────────────────────────────────

def _opp_from_row(row: dict) -> dict:
    meta = row.get("metadata") or {}
    company = meta.get("company") or {}
    return {
        "id": row.get("id"),
        "title": row.get("title") or "",
        "description": row.get("description") or "",
        "location": row.get("location") or "",
        "commitment_type": row.get("commitment_type") or "",
        "is_remote": row.get("is_remote"),
        "sector": row.get("industry") or company.get("sector") or "",
        "pay_range_min": row.get("pay_range_min"),
        "pay_range_max": row.get("pay_range_max"),
        "pay_range_currency": row.get("pay_range_currency") or "EUR",
        "company": company,
        "track": meta.get("track") or "",
        "created_at": row.get("created_at"),
    }


def list_opportunities(limit: int = 100) -> list[dict]:
    db = _db()
    rows = (db.table("opportunities").select("*")
            .eq("organization_id", MARKETPLACE_ORG_ID)
            .order("created_at", desc=True).limit(limit).execute().data) or []
    return [_opp_from_row(r) for r in rows if (r.get("metadata") or {}).get("marketplace")]


def get_opportunity(opp_id: str) -> Optional[dict]:
    db = _db()
    rows = db.table("opportunities").select("*").eq("id", opp_id).execute().data
    return _opp_from_row(rows[0]) if rows else None


def create_opportunity(*, title: str, company: dict, description: str = "",
                       location: str = "", commitment_type: str = "permanent",
                       is_remote: bool = True, sector: str = "", track: str = "",
                       pay_range_min: Optional[float] = None,
                       pay_range_max: Optional[float] = None,
                       pay_range_currency: str = "EUR",
                       opp_id: Optional[str] = None) -> dict:
    ensure_marketplace_org()
    db = _db()
    row_id = opp_id or str(uuid.uuid4())
    # opportunities.type and .commitment_type are enums. Map the marketplace's
    # engagement vocabulary onto DB-valid values; keep the display value in
    # metadata.engagement so the UI can show "Permanent"/"Fractional".
    db_commitment = "fractional" if commitment_type == "fractional" else "full_time"
    db_type = "hire_fractional"
    row = {
        "id": row_id,
        "organization_id": MARKETPLACE_ORG_ID,
        "created_by_user_id": _system_actor() or MARKETPLACE_ORG_ID,  # NOT NULL FK → users
        "type": db_type,
        "title": title,
        "description": description,
        "location": location,
        "commitment_type": db_commitment,
        "is_remote": is_remote,
        "industry": sector,
        "pay_range_min": pay_range_min,
        "pay_range_max": pay_range_max,
        "pay_range_currency": pay_range_currency,
        "status": "open",
        "metadata": {"marketplace": True, "company": company, "track": track,
                     "engagement": commitment_type},
    }
    existing = db.table("opportunities").select("id").eq("id", row_id).execute().data
    if existing:
        db.table("opportunities").update(row).eq("id", row_id).execute()
    else:
        db.table("opportunities").insert(row).execute()
    return _opp_from_row(row)


def list_companies() -> list[dict]:
    """Derive the distinct company list from marketplace opportunities."""
    seen: dict[str, dict] = {}
    for opp in list_opportunities():
        c = opp.get("company") or {}
        cid = c.get("id") or c.get("name")
        if cid and cid not in seen:
            seen[cid] = c
    return list(seen.values())


# ── Introductions (activity_log entity_type='placement') ─────────────────────

def compute_placement_fee(first_year_comp: Optional[float], fee_pct: float) -> Optional[float]:
    if first_year_comp is None:
        return None
    try:
        return round(float(first_year_comp) * float(fee_pct) / 100.0, 2)
    except (TypeError, ValueError):
        return None


def _intro_from_row(row: dict) -> dict:
    meta = row.get("metadata") or {}
    return {
        "id": row.get("id"),
        "org_id": row.get("organization_id"),
        "leader_id": meta.get("leader_id"),
        "leader_name": meta.get("leader_name"),
        "company": meta.get("company") or {},
        "opportunity_id": meta.get("opportunity_id"),
        "opportunity_title": meta.get("opportunity_title"),
        "status": meta.get("status") or "requested",
        "message": meta.get("message") or "",
        "first_year_comp": meta.get("first_year_comp"),
        "placement_fee_pct": meta.get("placement_fee_pct", DEFAULT_PLACEMENT_FEE_PCT),
        "placement_fee_amount": meta.get("placement_fee_amount"),
        "hired": bool(meta.get("hired")),
        "requested_by": row.get("actor_id"),
        "created_at": row.get("created_at"),
        "updated_at": meta.get("updated_at") or row.get("created_at"),
    }


def create_introduction(*, org_id: str, actor_id: str, leader_id: str, leader_name: str,
                        company: dict, opportunity_id: Optional[str] = None,
                        opportunity_title: Optional[str] = None, message: str = "",
                        first_year_comp: Optional[float] = None,
                        fee_pct: float = DEFAULT_PLACEMENT_FEE_PCT,
                        status: str = "requested", hired: bool = False,
                        intro_id: Optional[str] = None,
                        created_at: Optional[str] = None) -> dict:
    db = _db()
    row_id = intro_id or str(uuid.uuid4())
    fee_amount = compute_placement_fee(first_year_comp, fee_pct) if (hired or first_year_comp) else None
    meta = {
        "marketplace": True,
        "kind": "introduction",
        "leader_id": leader_id,
        "leader_name": leader_name,
        "company": company,
        "opportunity_id": opportunity_id,
        "opportunity_title": opportunity_title,
        "status": status,
        "message": message,
        "first_year_comp": first_year_comp,
        "placement_fee_pct": fee_pct,
        "placement_fee_amount": fee_amount,
        "hired": hired,
        "updated_at": created_at or _now(),
    }
    row = {
        "id": row_id,
        "organization_id": org_id,
        "entity_type": INTRO_ENTITY_TYPE,
        "entity_id": leader_id,
        "activity_type": "marketplace_introduction",
        "actor_id": actor_id,
        "summary": f"Introduction requested: {leader_name}"
                   + (f" → {company.get('name')}" if company.get("name") else ""),
        "metadata": meta,
    }
    insert = dict(row)
    if created_at:
        insert["created_at"] = created_at
    db.table("activity_log").insert(insert).execute()
    return _intro_from_row(row)


def list_introductions(*, org_id: Optional[str] = None, limit: int = 200) -> list[dict]:
    """List marketplace introductions.

    In this MVP the operator (admin) view is marketplace-wide: intros are
    returned across orgs (the marketplace operates a single pipeline). Passing
    org_id filters to one buyer org. See SHIPPED.md — per-tenant demand-side
    scoping is a documented later step.
    """
    db = _db()
    q = (db.table("activity_log").select("*")
         .eq("entity_type", INTRO_ENTITY_TYPE)
         .eq("activity_type", "marketplace_introduction")
         .order("created_at", desc=True).limit(limit))
    if org_id:
        q = q.eq("organization_id", org_id)
    rows = q.execute().data or []
    return [_intro_from_row(r) for r in rows if (r.get("metadata") or {}).get("marketplace")]


def get_introduction(intro_id: str) -> Optional[dict]:
    db = _db()
    rows = db.table("activity_log").select("*").eq("id", intro_id).execute().data
    return _intro_from_row(rows[0]) if rows else None


def update_introduction(intro_id: str, *, status: Optional[str] = None,
                        hired: Optional[bool] = None,
                        first_year_comp: Optional[float] = None,
                        fee_pct: Optional[float] = None) -> Optional[dict]:
    db = _db()
    rows = db.table("activity_log").select("*").eq("id", intro_id).execute().data
    if not rows:
        return None
    row = rows[0]
    meta = dict(row.get("metadata") or {})
    if status is not None:
        meta["status"] = status
    if hired is not None:
        meta["hired"] = hired
        if hired and meta.get("status") not in ("hired",):
            meta["status"] = "hired"
    if first_year_comp is not None:
        meta["first_year_comp"] = first_year_comp
    if fee_pct is not None:
        meta["placement_fee_pct"] = fee_pct
    # Recompute fee whenever comp or pct is known.
    meta["placement_fee_amount"] = compute_placement_fee(
        meta.get("first_year_comp"), meta.get("placement_fee_pct", DEFAULT_PLACEMENT_FEE_PCT)
    )
    meta["updated_at"] = _now()
    db.table("activity_log").update({"metadata": meta}).eq("id", intro_id).execute()
    return _intro_from_row({**row, "metadata": meta})


# ── Teardown (idempotent re-seed support) ────────────────────────────────────

def purge_marketplace() -> dict:
    """Delete every marketplace-namespaced row. Used before a fresh seed."""
    db = _db()
    counts = {}
    intros = (db.table("activity_log").select("id")
              .eq("entity_type", INTRO_ENTITY_TYPE)
              .eq("activity_type", "marketplace_introduction").execute().data) or []
    for r in intros:
        db.table("activity_log").delete().eq("id", r["id"]).execute()
    counts["introductions"] = len(intros)
    opps = (db.table("opportunities").select("id")
            .eq("organization_id", MARKETPLACE_ORG_ID).execute().data) or []
    for r in opps:
        db.table("opportunities").delete().eq("id", r["id"]).execute()
    counts["opportunities"] = len(opps)
    leaders = (db.table("people_profiles").select("id")
               .eq("organization_id", MARKETPLACE_ORG_ID).execute().data) or []
    for r in leaders:
        db.table("people_profiles").delete().eq("id", r["id"]).execute()
    counts["leaders"] = len(leaders)
    return counts
