"""Marketplace persistence — dedicated tables (graduated from the namespaced store).

See DECISIONS.md D-14/D-17/D-19. The marketplace now runs on its own tables
(applied to prod via MARKETPLACE_MIGRATION.sql):
  marketplace_leaders               — supply side, linked to an auth account (user_id)
  marketplace_vetting_assessments   — per-leader vetting audit (ON DELETE CASCADE)
  marketplace_companies             — demand-side company profiles (one per org)
  marketplace_opportunities         — roles (company_id FK)
  marketplace_introductions         — the billable event (leader_id FK, company JSONB)

Design carried over from the namespaced store:
- A leader profile is LINKED to its owner via marketplace_leaders.user_id (UNIQUE
  where not null — one profile per account). No more source_metadata workaround.
- Private contact (marketplace_leaders.contact JSONB) is serialized out only to
  the owner or a company on an accepted introduction.
- Introductions carry leader_response + contact_revealed; a company sees the
  leader's contact only after acceptance.

Public function signatures and serialized shapes are UNCHANGED from the
namespaced version, so routes, the frontend, and the tests are unaffected.

Every function is defensive: the Supabase client is fetched lazily. Queries use
only verbs the test fake supports (select/eq/order/limit/insert/update/delete)
and do richer filtering in Python — no .in_()/.not_.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from services.marketplace.constants import (
    MARKETPLACE_ORG_ID,
    DEFAULT_PLACEMENT_FEE_PCT,
    CONTACT_REVEALED_STATES,
    TRACK_LABELS,
)

LEADERS_TBL = "marketplace_leaders"
VETTING_TBL = "marketplace_vetting_assessments"
COMPANIES_TBL = "marketplace_companies"
OPPS_TBL = "marketplace_opportunities"
INTROS_TBL = "marketplace_introductions"


def _db():
    from config.clients import supabase_client
    return supabase_client


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_company_org_id(company: dict) -> str:
    """Deterministic synthetic org_id for a demo/seed company (satisfies the
    NOT NULL + UNIQUE(org_id) constraint without colliding with real orgs)."""
    key = company.get("id") or company.get("name") or "unknown"
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"mkt-seed-company:{key}"))


# ── Compatibility no-op (was the namespace anchor) ───────────────────────────

def ensure_marketplace_org() -> str:
    """No-op on dedicated tables — retained so callers/seeder are unchanged."""
    return MARKETPLACE_ORG_ID


# ── Leaders ──────────────────────────────────────────────────────────────────

def _leader_from_row(row: dict, *, include_contact: bool = False) -> dict:
    vet = row.get("vetting") or {}
    name = row.get("name") or "AI Leader"
    track = row.get("track") or ""
    out = {
        "id": row.get("id"),
        "user_id": row.get("user_id"),
        "name": name,
        "headline": row.get("headline") or "",
        "bio": row.get("bio") or "",
        "location": row.get("location") or "",
        "skills": row.get("skills") or [],
        "sectors": row.get("sectors") or [],
        "seniority": row.get("seniority") or "",
        "track": track,
        "discipline": TRACK_LABELS.get(track, track.replace("_", " ").title() if track else ""),
        "engagement": row.get("engagement") or "both",
        "comp_expectation": row.get("comp_expectation") or "",
        "years_experience": row.get("years_experience") or 0,
        "vetting_status": row.get("vetting_status") or "pending",
        "vetting_score": row.get("vetting_score") if row.get("vetting_score") is not None else vet.get("score"),
        "vetting": vet or None,
        "avatar_initials": "".join(p[0] for p in name.split()[:2]).upper() if name else "AI",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at") or row.get("created_at"),
    }
    if include_contact:
        out["contact"] = row.get("contact") or {}
    return out


def list_leaders(*, status: Optional[str] = "verified", skill: Optional[str] = None,
                 seniority: Optional[str] = None, engagement: Optional[str] = None,
                 sector: Optional[str] = None, track: Optional[str] = None,
                 limit: int = 500) -> list[dict]:
    """List curated leaders (global catalog read). Contact never included here."""
    db = _db()
    rows = (db.table(LEADERS_TBL).select("*")
            .order("created_at", desc=True).limit(limit).execute().data) or []
    leaders = [_leader_from_row(r) for r in rows]

    def keep(ld: dict) -> bool:
        if status and ld["vetting_status"] != status:
            return False
        if skill and not any(skill.lower() in (s or "").lower() for s in ld["skills"]):
            return False
        if seniority and seniority.lower() not in (ld["seniority"] or "").lower():
            return False
        if engagement and engagement != "both":
            if ld["engagement"] not in (engagement, "both"):
                return False
        if sector and not any(sector.lower() in (s or "").lower() for s in ld["sectors"]):
            return False
        if track and ld["track"] != track:
            return False
        return True

    return [ld for ld in leaders if keep(ld)]


def get_leader(leader_id: str, *, include_contact: bool = False) -> Optional[dict]:
    db = _db()
    rows = db.table(LEADERS_TBL).select("*").eq("id", leader_id).execute().data
    return _leader_from_row(rows[0], include_contact=include_contact) if rows else None


def get_leader_by_user(user_id: str, *, include_contact: bool = True) -> Optional[dict]:
    """Find the marketplace leader profile owned by an auth account, if any."""
    if not user_id:
        return None
    db = _db()
    rows = db.table(LEADERS_TBL).select("*").eq("user_id", user_id).execute().data or []
    return _leader_from_row(rows[0], include_contact=include_contact) if rows else None


def create_leader(*, name: str, headline: str, bio: str = "", location: str = "",
                  skills: Optional[list] = None, sectors: Optional[list] = None,
                  seniority: str = "", track: str = "", engagement: str = "both",
                  comp_expectation: str = "", years_experience: int = 0,
                  leader_id: Optional[str] = None, vetting: Optional[dict] = None,
                  vetting_status: str = "pending", user_id: Optional[str] = None,
                  contact: Optional[dict] = None) -> dict:
    """Create (or upsert by id) a marketplace leader."""
    db = _db()
    row_id = leader_id or str(uuid.uuid4())
    vetting = vetting or {}
    row = {
        "id": row_id,
        "name": name,
        "headline": headline,
        "bio": bio,
        "location": location,
        "skills": skills or [],
        "sectors": sectors or [],
        "seniority": seniority,
        "track": track,
        "engagement": engagement if engagement in ("fractional", "permanent", "both") else "both",
        "comp_expectation": comp_expectation,
        "years_experience": years_experience,
        "contact": contact or {},
        "vetting_status": vetting_status,
        "vetting": vetting or None,
        "vetting_score": vetting.get("score"),
        "updated_at": _now(),
    }
    if user_id:
        row["user_id"] = user_id
    existing = db.table(LEADERS_TBL).select("id").eq("id", row_id).execute().data
    if existing:
        db.table(LEADERS_TBL).update(row).eq("id", row_id).execute()
    else:
        db.table(LEADERS_TBL).insert(row).execute()
    return _leader_from_row(row, include_contact=True)


def update_leader(leader_id: str, *, name: Optional[str] = None, headline: Optional[str] = None,
                  bio: Optional[str] = None, location: Optional[str] = None,
                  skills: Optional[list] = None, sectors: Optional[list] = None,
                  seniority: Optional[str] = None, track: Optional[str] = None,
                  engagement: Optional[str] = None, comp_expectation: Optional[str] = None,
                  years_experience: Optional[int] = None,
                  contact: Optional[dict] = None) -> Optional[dict]:
    """Patch an existing leader profile. Only provided fields are changed."""
    db = _db()
    rows = db.table(LEADERS_TBL).select("*").eq("id", leader_id).execute().data
    if not rows:
        return None
    row = rows[0]
    upd: dict[str, Any] = {}
    for col, val in (
        ("name", name), ("headline", headline), ("bio", bio), ("location", location),
        ("skills", skills), ("sectors", sectors), ("seniority", seniority),
        ("track", track), ("engagement", engagement), ("comp_expectation", comp_expectation),
        ("years_experience", years_experience),
    ):
        if val is not None:
            upd[col] = val
    if contact is not None:
        merged = dict(row.get("contact") or {})
        merged.update({k: v for k, v in contact.items()})
        upd["contact"] = merged
    upd["updated_at"] = _now()
    db.table(LEADERS_TBL).update(upd).eq("id", leader_id).execute()
    return get_leader(leader_id, include_contact=True)


def set_leader_vetting(leader_id: str, vetting: dict, status: str) -> Optional[dict]:
    """Persist a vetting result onto a leader, update status, and log an audit row."""
    db = _db()
    rows = db.table(LEADERS_TBL).select("id").eq("id", leader_id).execute().data
    if not rows:
        return None
    db.table(LEADERS_TBL).update({
        "vetting": vetting,
        "vetting_status": status,
        "vetting_score": vetting.get("score"),
        "updated_at": _now(),
    }).eq("id", leader_id).execute()
    # Audit trail — one immutable assessment row per scoring.
    try:
        db.table(VETTING_TBL).insert({
            "id": str(uuid.uuid4()),
            "leader_id": leader_id,
            "track": vetting.get("track") or "",
            "questions": [],
            "responses": [],
            "score": vetting.get("score"),
            "passed": vetting.get("passed"),
            "rationale": vetting.get("rationale"),
            "per_competency": vetting.get("per_competency") or [],
            "model_used": vetting.get("model_used"),
            "ai_generated": bool(vetting.get("ai_generated")),
        }).execute()
    except Exception:
        pass  # audit is best-effort; never block the vetting result
    return get_leader(leader_id)


def record_vetting_attempt(leader_id: str) -> int:
    """Increment and return the persisted vetting-attempt counter."""
    db = _db()
    rows = db.table(LEADERS_TBL).select("vetting_attempts").eq("id", leader_id).execute().data
    if not rows:
        return 0
    count = int(rows[0].get("vetting_attempts") or 0) + 1
    db.table(LEADERS_TBL).update({"vetting_attempts": count}).eq("id", leader_id).execute()
    return count


def delete_leader(leader_id: str) -> bool:
    """GDPR erasure. Removes the leader's introductions first (the FK has no
    ON DELETE), then the leader (vetting_assessments cascade)."""
    db = _db()
    rows = db.table(LEADERS_TBL).select("id").eq("id", leader_id).execute().data
    if not rows:
        return False
    # Remove dependent introductions (leader_id FK is RESTRICT at the DB level).
    intros = db.table(INTROS_TBL).select("id").eq("leader_id", leader_id).execute().data or []
    for r in intros:
        db.table(INTROS_TBL).delete().eq("id", r["id"]).execute()
    db.table(LEADERS_TBL).delete().eq("id", leader_id).execute()
    return True


def export_leader(leader_id: str) -> Optional[dict]:
    """GDPR access: assemble everything held about a leader."""
    leader = get_leader(leader_id, include_contact=True)
    if not leader:
        return None
    db = _db()
    assessments = db.table(VETTING_TBL).select("*").eq("leader_id", leader_id).execute().data or []
    intros = list_introductions_for_leader(leader_id, reveal_company_contact=True)
    return {
        "profile": leader,
        "vetting": leader.get("vetting"),
        "vetting_assessments": assessments,
        "introductions": intros,
        "exported_at": _now(),
        "notice": ("This is the complete set of data ainm Marketplace holds about "
                   "your leader profile. To erase it, use the delete endpoint or "
                   "email compliance@ainm.ai."),
    }


# ── Companies + opportunities ────────────────────────────────────────────────

def _company_from_row(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "org_id": row.get("org_id"),
        "name": row.get("name") or "",
        "sector": row.get("sector") or "",
        "size": row.get("size") or "",
        "location": row.get("location") or "",
        "website": row.get("website") or "",
        "description": row.get("description") or "",
        "contact_name": row.get("contact_name") or "",
        "contact_email": row.get("contact_email") or "",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at") or row.get("created_at"),
    }


def get_company_profile(org_id: str) -> Optional[dict]:
    """Return the marketplace company profile owned by an org, if any."""
    if not org_id:
        return None
    db = _db()
    rows = db.table(COMPANIES_TBL).select("*").eq("org_id", org_id).execute().data or []
    return _company_from_row(rows[0]) if rows else None


def _upsert_company(company: dict, *, org_id: Optional[str] = None) -> str:
    """Upsert a company row (by id when present, else by org_id). Returns id."""
    db = _db()
    company = dict(company or {})
    cid = company.get("id") or str(uuid.uuid4())
    org = org_id or company.get("org_id") or _seed_company_org_id(company)
    row = {
        "id": cid,
        "org_id": org,
        "name": company.get("name") or "",
        "sector": company.get("sector"),
        "size": company.get("size"),
        "location": company.get("location"),
        "website": company.get("website"),
        "description": company.get("description"),
        "contact_name": company.get("contact_name"),
        "contact_email": company.get("contact_email"),
        "updated_at": _now(),
    }
    existing = db.table(COMPANIES_TBL).select("id").eq("id", cid).execute().data
    if existing:
        db.table(COMPANIES_TBL).update(row).eq("id", cid).execute()
    else:
        db.table(COMPANIES_TBL).insert(row).execute()
    return cid


def upsert_company_profile(*, org_id: str, actor_id: str, **fields) -> dict:
    """Create or update the caller org's company profile (one per org)."""
    db = _db()
    existing = db.table(COMPANIES_TBL).select("*").eq("org_id", org_id).execute().data or []
    clean = {k: v for k, v in fields.items() if v is not None}
    if existing:
        row = existing[0]
        upd = {**clean, "updated_at": _now()}
        db.table(COMPANIES_TBL).update(upd).eq("id", row["id"]).execute()
        return _company_from_row({**row, **upd})
    cid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"mkt-company:{org_id}"))
    row = {"id": cid, "org_id": org_id, **clean, "updated_at": _now()}
    db.table(COMPANIES_TBL).insert(row).execute()
    return _company_from_row(row)


def _companies_map() -> dict[str, dict]:
    db = _db()
    rows = db.table(COMPANIES_TBL).select("*").limit(1000).execute().data or []
    return {r["id"]: _company_from_row(r) for r in rows}


def _opp_from_row(row: dict, companies: Optional[dict] = None) -> dict:
    company = {}
    cid = row.get("company_id")
    if cid:
        if companies is not None:
            company = companies.get(cid) or {}
        else:
            db = _db()
            crows = db.table(COMPANIES_TBL).select("*").eq("id", cid).execute().data
            company = _company_from_row(crows[0]) if crows else {}
    return {
        "id": row.get("id"),
        "title": row.get("title") or "",
        "description": row.get("description") or "",
        "location": row.get("location") or "",
        "commitment_type": row.get("commitment_type") or "",
        "is_remote": row.get("is_remote"),
        "sector": row.get("sector") or company.get("sector") or "",
        "pay_range_min": row.get("pay_range_min"),
        "pay_range_max": row.get("pay_range_max"),
        "pay_range_currency": row.get("pay_range_currency") or "EUR",
        "company": company,
        "track": row.get("track") or "",
        "created_at": row.get("created_at"),
    }


def list_opportunities(limit: int = 100) -> list[dict]:
    db = _db()
    rows = (db.table(OPPS_TBL).select("*")
            .order("created_at", desc=True).limit(limit).execute().data) or []
    companies = _companies_map()
    return [_opp_from_row(r, companies) for r in rows]


def get_opportunity(opp_id: str) -> Optional[dict]:
    db = _db()
    rows = db.table(OPPS_TBL).select("*").eq("id", opp_id).execute().data
    return _opp_from_row(rows[0]) if rows else None


def create_opportunity(*, title: str, company: dict, description: str = "",
                       location: str = "", commitment_type: str = "permanent",
                       is_remote: bool = True, sector: str = "", track: str = "",
                       pay_range_min: Optional[float] = None,
                       pay_range_max: Optional[float] = None,
                       pay_range_currency: str = "EUR",
                       opp_id: Optional[str] = None,
                       org_id: Optional[str] = None,
                       created_by_user_id: Optional[str] = None) -> dict:
    db = _db()
    row_id = opp_id or str(uuid.uuid4())
    company_id = _upsert_company(company, org_id=org_id) if company else None
    row = {
        "id": row_id,
        "company_id": company_id,
        "org_id": org_id or MARKETPLACE_ORG_ID,
        "title": title,
        "description": description,
        "track": track,
        "sector": sector,
        "commitment_type": commitment_type,
        "location": location,
        "is_remote": is_remote,
        "pay_range_min": pay_range_min,
        "pay_range_max": pay_range_max,
        "pay_range_currency": pay_range_currency,
        "status": "open",
    }
    existing = db.table(OPPS_TBL).select("id").eq("id", row_id).execute().data
    if existing:
        db.table(OPPS_TBL).update(row).eq("id", row_id).execute()
    else:
        db.table(OPPS_TBL).insert(row).execute()
    return _opp_from_row(row)


def list_companies() -> list[dict]:
    """Distinct demand-side companies that have opportunities (the demand catalog)."""
    seen: dict[str, dict] = {}
    for opp in list_opportunities():
        c = opp.get("company") or {}
        cid = c.get("id") or c.get("name")
        if cid and cid not in seen:
            seen[cid] = c
    return list(seen.values())


# ── Introductions ────────────────────────────────────────────────────────────

def compute_placement_fee(first_year_comp: Optional[float], fee_pct: float) -> Optional[float]:
    if first_year_comp is None:
        return None
    try:
        return round(float(first_year_comp) * float(fee_pct) / 100.0, 2)
    except (TypeError, ValueError):
        return None


def _leader_name_map() -> dict[str, str]:
    """id → name for all leaders (leader_name is resolved from the FK, not stored
    on the introduction — the dedicated intros table has no leader_name column)."""
    db = _db()
    rows = db.table(LEADERS_TBL).select("id,name").limit(2000).execute().data or []
    return {r["id"]: r.get("name") for r in rows}


def _leader_name(leader_id: str) -> Optional[str]:
    if not leader_id:
        return None
    db = _db()
    rows = db.table(LEADERS_TBL).select("name").eq("id", leader_id).execute().data
    return rows[0].get("name") if rows else None


def _intro_from_row(row: dict, *, viewer_org_id: Optional[str] = None,
                    viewer_is_leader: bool = False,
                    leader_name: Optional[str] = None) -> dict:
    status = row.get("status") or "requested"
    contact_revealed = bool(row.get("contact_revealed")) or status in CONTACT_REVEALED_STATES
    out = {
        "id": row.get("id"),
        "org_id": row.get("organization_id"),
        "leader_id": row.get("leader_id"),
        "leader_name": leader_name if leader_name is not None else row.get("leader_name"),
        "company": row.get("company") or {},
        "opportunity_id": row.get("opportunity_id"),
        "opportunity_title": row.get("opportunity_title"),
        "status": status,
        "leader_response": row.get("leader_response") or (
            "accepted" if status in CONTACT_REVEALED_STATES
            else "declined" if status == "declined" else "pending"),
        "message": row.get("message") or "",
        "first_year_comp": row.get("first_year_comp"),
        "placement_fee_pct": row.get("placement_fee_pct", DEFAULT_PLACEMENT_FEE_PCT),
        "placement_fee_amount": row.get("placement_fee_amount"),
        "hired": bool(row.get("hired")),
        "outcome": row.get("outcome"),
        "contact_revealed": contact_revealed,
        "requested_by": row.get("requested_by"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at") or row.get("created_at"),
    }
    is_requesting_company = viewer_org_id is not None and viewer_org_id == row.get("organization_id")
    if is_requesting_company and contact_revealed:
        out["leader_contact"] = row.get("leader_contact") or {}
    if viewer_is_leader:
        out["requester_contact"] = row.get("requester_contact") or {}
    return out


def create_introduction(*, org_id: str, actor_id: str, leader_id: str, leader_name: str,
                        company: dict, opportunity_id: Optional[str] = None,
                        opportunity_title: Optional[str] = None, message: str = "",
                        first_year_comp: Optional[float] = None,
                        fee_pct: float = DEFAULT_PLACEMENT_FEE_PCT,
                        status: str = "requested", hired: bool = False,
                        intro_id: Optional[str] = None,
                        created_at: Optional[str] = None,
                        requester_contact: Optional[dict] = None,
                        leader_contact: Optional[dict] = None) -> dict:
    db = _db()
    row_id = intro_id or str(uuid.uuid4())
    fee_amount = compute_placement_fee(first_year_comp, fee_pct) if (hired or first_year_comp) else None
    leader_response = "accepted" if status in CONTACT_REVEALED_STATES else "declined" if status == "declined" else "pending"
    # opportunity_id is a FK; keep it only if it resolves to a real row.
    opp_ok = False
    if opportunity_id:
        opp_ok = bool(db.table(OPPS_TBL).select("id").eq("id", opportunity_id).execute().data)
    row = {
        "id": row_id,
        "organization_id": org_id,
        "requested_by": actor_id,
        "leader_id": leader_id,
        "company": company or {},
        "opportunity_id": opportunity_id if opp_ok else None,
        "opportunity_title": opportunity_title,
        "status": status,
        "leader_response": leader_response,
        "message": message,
        "requester_contact": requester_contact or {},
        "leader_contact": leader_contact or {},
        "contact_revealed": status in CONTACT_REVEALED_STATES,
        "first_year_comp": first_year_comp,
        "placement_fee_pct": fee_pct,
        "placement_fee_amount": fee_amount,
        "hired": hired,
        "updated_at": created_at or _now(),
    }
    if created_at:
        row["created_at"] = created_at
    db.table(INTROS_TBL).insert(row).execute()
    return _intro_from_row(row, viewer_org_id=org_id, leader_name=leader_name)


def _all_intro_rows(limit: int = 500) -> list[dict]:
    db = _db()
    return (db.table(INTROS_TBL).select("*")
            .order("created_at", desc=True).limit(limit).execute().data) or []


def list_introductions(*, org_id: Optional[str] = None, limit: int = 500) -> list[dict]:
    """List introductions. org_id scopes to a single buyer org (tenant isolation)."""
    db = _db()
    if org_id:
        rows = (db.table(INTROS_TBL).select("*")
                .eq("organization_id", org_id)
                .order("created_at", desc=True).limit(limit).execute().data) or []
    else:
        rows = _all_intro_rows(limit=limit)
    names = _leader_name_map()
    return [_intro_from_row(r, viewer_org_id=org_id, leader_name=names.get(r.get("leader_id")))
            for r in rows]


def list_introductions_for_leader(leader_id: str, *, limit: int = 500,
                                  reveal_company_contact: bool = True) -> list[dict]:
    """Introductions requested TO a given leader (the leader's inbox)."""
    db = _db()
    rows = (db.table(INTROS_TBL).select("*")
            .eq("leader_id", leader_id)
            .order("created_at", desc=True).limit(limit).execute().data) or []
    name = _leader_name(leader_id)
    return [_intro_from_row(r, viewer_is_leader=reveal_company_contact, leader_name=name)
            for r in rows]


def get_introduction(intro_id: str, *, viewer_org_id: Optional[str] = None,
                     viewer_is_leader: bool = False) -> Optional[dict]:
    db = _db()
    rows = db.table(INTROS_TBL).select("*").eq("id", intro_id).execute().data
    if not rows:
        return None
    return _intro_from_row(rows[0], viewer_org_id=viewer_org_id, viewer_is_leader=viewer_is_leader,
                           leader_name=_leader_name(rows[0].get("leader_id")))


def respond_to_introduction(intro_id: str, *, leader_id: str, decision: str,
                            leader_contact: Optional[dict] = None) -> Optional[dict]:
    """Leader accepts or declines an introduction request."""
    db = _db()
    rows = db.table(INTROS_TBL).select("*").eq("id", intro_id).execute().data
    if not rows:
        return None
    row = rows[0]
    if row.get("leader_id") != leader_id:
        return None
    upd: dict[str, Any] = {"updated_at": _now()}
    if decision == "accepted":
        upd.update({"leader_response": "accepted", "status": "accepted", "contact_revealed": True})
        if leader_contact:
            upd["leader_contact"] = leader_contact
    else:
        upd.update({"leader_response": "declined", "status": "declined", "contact_revealed": False})
    db.table(INTROS_TBL).update(upd).eq("id", intro_id).execute()
    return _intro_from_row({**row, **upd}, viewer_is_leader=True,
                           leader_name=_leader_name(leader_id))


def update_introduction(intro_id: str, *, status: Optional[str] = None,
                        hired: Optional[bool] = None,
                        first_year_comp: Optional[float] = None,
                        fee_pct: Optional[float] = None,
                        outcome: Optional[str] = None,
                        viewer_org_id: Optional[str] = None) -> Optional[dict]:
    db = _db()
    rows = db.table(INTROS_TBL).select("*").eq("id", intro_id).execute().data
    if not rows:
        return None
    row = rows[0]
    upd: dict[str, Any] = {}
    new_status = row.get("status")
    if status is not None:
        upd["status"] = status
        new_status = status
    if hired is not None:
        upd["hired"] = hired
        if hired and new_status != "hired":
            upd["status"] = "hired"
            new_status = "hired"
    if outcome is not None:
        upd["outcome"] = outcome
    comp = first_year_comp if first_year_comp is not None else row.get("first_year_comp")
    pct = fee_pct if fee_pct is not None else row.get("placement_fee_pct", DEFAULT_PLACEMENT_FEE_PCT)
    if first_year_comp is not None:
        upd["first_year_comp"] = first_year_comp
    if fee_pct is not None:
        upd["placement_fee_pct"] = fee_pct
    upd["placement_fee_amount"] = compute_placement_fee(comp, pct)
    upd["updated_at"] = _now()
    db.table(INTROS_TBL).update(upd).eq("id", intro_id).execute()
    return _intro_from_row({**row, **upd}, viewer_org_id=viewer_org_id or row.get("organization_id"),
                           leader_name=_leader_name(row.get("leader_id")))


# ── Teardown (seed-only; never touches real signups) ─────────────────────────

def purge_marketplace() -> dict:
    """Delete ONLY the synthetic seed rows (by their deterministic ids). Real
    leader/company/intro rows created by actual users are preserved."""
    from services.marketplace.seed_data import LEADERS, OPPORTUNITIES, COMPANIES, INTRODUCTIONS
    db = _db()
    counts = {"introductions": 0, "opportunities": 0, "leaders": 0, "companies": 0}
    for intro in INTRODUCTIONS:
        r = db.table(INTROS_TBL).delete().eq("id", intro["id"]).execute()
        counts["introductions"] += 1
    # Also remove any intros addressed to seed leaders (defensive).
    for ld in LEADERS:
        rows = db.table(INTROS_TBL).select("id").eq("leader_id", ld["id"]).execute().data or []
        for r in rows:
            db.table(INTROS_TBL).delete().eq("id", r["id"]).execute()
    for op in OPPORTUNITIES:
        db.table(OPPS_TBL).delete().eq("id", op["id"]).execute()
        counts["opportunities"] += 1
    for ld in LEADERS:
        db.table(LEADERS_TBL).delete().eq("id", ld["id"]).execute()  # vetting cascades
        counts["leaders"] += 1
    for c in COMPANIES:
        db.table(COMPANIES_TBL).delete().eq("id", c["id"]).execute()
        counts["companies"] += 1
    return counts
