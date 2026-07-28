"""Marketplace persistence — maps marketplace entities onto existing durable tables.

See DECISIONS.md D-14 and D-17. No new prod DDL is required: leaders live in
people_profiles (namespaced by MARKETPLACE_ORG_ID + source='marketplace_leader'),
companies+roles in opportunities (metadata.marketplace), company profiles and the
billable introductions in activity_log (entity_type='placement', metadata.marketplace).

Real-user model (v2):
- A leader profile is LINKED to its owner's auth account via people_profiles.user_id.
  This lets the owner edit it, take vetting, and see/answer introduction requests.
- A leader's private contact details live in source_metadata.contact and are only
  serialized out when the caller is entitled to them (the owner, or a company on an
  accepted introduction). The public catalog never exposes contact.
- Introductions carry a leader_response ('pending'|'accepted'|'declined') and a
  contact_revealed flag; a company sees the leader's contact only after acceptance.

Every function is defensive: the Supabase client is fetched lazily so imports
never fail, and reads tolerate missing/legacy rows. Queries use only the query
verbs the test fake supports (select/eq/order/limit/insert/update/delete) and do
any richer filtering in Python.
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
    INTRO_ACTIVITY_TYPE,
    COMPANY_ACTIVITY_TYPE,
    DEFAULT_PLACEMENT_FEE_PCT,
    CONTACT_REVEALED_STATES,
    TRACK_LABELS,
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

def _leader_from_row(row: dict, *, include_contact: bool = False) -> dict:
    """Serialize a people_profiles row into the marketplace leader shape.

    Private contact details are included ONLY when include_contact is True (the
    caller is the profile owner or a company with an accepted introduction).
    """
    meta = row.get("source_metadata") or {}
    vet = meta.get("vetting") or {}
    first = row.get("first_name") or ""
    last = row.get("last_name") or ""
    name = f"{first} {last}".strip() or meta.get("display_name") or "AI Leader"
    track = meta.get("track") or ""
    out = {
        "id": row.get("id"),
        # Owning auth account. Stored in source_metadata (NOT the people_profiles
        # user_id column) because that column has a GLOBAL unique constraint and
        # the onboarding hook already claims each user_id for their own-org row.
        "user_id": meta.get("owner_user_id") or row.get("user_id"),
        "name": name,
        "headline": row.get("headline") or meta.get("headline") or "",
        "bio": row.get("bio") or "",
        "location": row.get("location") or "",
        "skills": meta.get("skills") or row.get("skills") or [],
        "sectors": meta.get("sectors") or row.get("industries") or [],
        "seniority": meta.get("seniority") or "",
        "track": track,
        "discipline": TRACK_LABELS.get(track, track.replace("_", " ").title() if track else ""),
        "engagement": meta.get("engagement") or row.get("availability_type") or "both",
        "comp_expectation": meta.get("comp_expectation") or row.get("rate_range") or "",
        "years_experience": row.get("years_experience") or meta.get("years_experience") or 0,
        "vetting_status": meta.get("vetting_status") or "pending",
        "vetting_score": vet.get("score"),
        "vetting": vet or None,
        "avatar_initials": "".join(p[0] for p in name.split()[:2]).upper() if name else "AI",
        "created_at": row.get("created_at"),
        "updated_at": meta.get("updated_at") or row.get("created_at"),
    }
    if include_contact:
        out["contact"] = meta.get("contact") or {}
    return out


def list_leaders(*, status: Optional[str] = "verified", skill: Optional[str] = None,
                 seniority: Optional[str] = None, engagement: Optional[str] = None,
                 sector: Optional[str] = None, track: Optional[str] = None,
                 limit: int = 500) -> list[dict]:
    """List curated leaders (global catalog read — intentionally not org-scoped).

    Filters are applied in Python against the JSONB-derived shape so the demo
    can filter on marketplace-specific fields the base table doesn't index.
    Contact details are never included here — this is the public catalog.
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


def get_leader(leader_id: str, *, include_contact: bool = False) -> Optional[dict]:
    db = _db()
    rows = (db.table("people_profiles").select("*")
            .eq("id", leader_id).eq("organization_id", MARKETPLACE_ORG_ID).execute().data)
    return _leader_from_row(rows[0], include_contact=include_contact) if rows else None


def get_leader_by_user(user_id: str, *, include_contact: bool = True) -> Optional[dict]:
    """Find the marketplace leader profile owned by an auth account, if any.

    Ownership is keyed on source_metadata.owner_user_id (see _leader_from_row),
    so we read the namespaced pool and match in Python — the people_profiles
    user_id column is globally unique and reserved by the onboarding hook.
    """
    if not user_id:
        return None
    db = _db()
    rows = (db.table("people_profiles").select("*")
            .eq("organization_id", MARKETPLACE_ORG_ID)
            .limit(1000).execute().data) or []
    for r in rows:
        meta = r.get("source_metadata") or {}
        if meta.get("owner_user_id") == user_id:
            return _leader_from_row(r, include_contact=include_contact)
    return None


def create_leader(*, name: str, headline: str, bio: str = "", location: str = "",
                  skills: Optional[list] = None, sectors: Optional[list] = None,
                  seniority: str = "", track: str = "", engagement: str = "both",
                  comp_expectation: str = "", years_experience: int = 0,
                  leader_id: Optional[str] = None, vetting: Optional[dict] = None,
                  vetting_status: str = "pending", user_id: Optional[str] = None,
                  contact: Optional[dict] = None) -> dict:
    """Create (or upsert by id) a marketplace leader in people_profiles."""
    ensure_marketplace_org()
    db = _db()
    parts = name.split()
    first = parts[0] if parts else name
    last = " ".join(parts[1:]) if len(parts) > 1 else ""
    row_id = leader_id or str(uuid.uuid4())
    source_metadata = {
        "marketplace": True,
        "source": LEADER_SOURCE,
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
        "contact": contact or {},
        "updated_at": _now(),
    }
    if user_id:
        source_metadata["owner_user_id"] = user_id
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
    # NOTE: we deliberately do NOT set the people_profiles.user_id column — it is
    # globally unique and already claimed by the onboarding hook for the user's
    # own-org row. Ownership lives in source_metadata.owner_user_id.
    existing = db.table("people_profiles").select("id").eq("id", row_id).execute().data
    if existing:
        db.table("people_profiles").update(row).eq("id", row_id).execute()
    else:
        db.table("people_profiles").insert(row).execute()
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
    rows = (db.table("people_profiles").select("*")
            .eq("id", leader_id).eq("organization_id", MARKETPLACE_ORG_ID).execute().data)
    if not rows:
        return None
    row = rows[0]
    meta = dict(row.get("source_metadata") or {})
    col_update: dict[str, Any] = {}
    if name is not None:
        parts = name.split()
        col_update["first_name"] = parts[0] if parts else name
        col_update["last_name"] = " ".join(parts[1:]) if len(parts) > 1 else ""
        meta["display_name"] = name
    if headline is not None:
        col_update["headline"] = headline
        meta["headline"] = headline
    if bio is not None:
        col_update["bio"] = bio
    if location is not None:
        col_update["location"] = location
    if years_experience is not None:
        col_update["years_experience"] = years_experience
        meta["years_experience"] = years_experience
    if skills is not None:
        meta["skills"] = skills
    if sectors is not None:
        meta["sectors"] = sectors
    if seniority is not None:
        meta["seniority"] = seniority
    if track is not None:
        meta["track"] = track
    if engagement is not None:
        meta["engagement"] = engagement
    if comp_expectation is not None:
        meta["comp_expectation"] = comp_expectation
    if contact is not None:
        merged = dict(meta.get("contact") or {})
        merged.update({k: v for k, v in contact.items()})
        meta["contact"] = merged
    meta["updated_at"] = _now()
    col_update["source_metadata"] = meta
    db.table("people_profiles").update(col_update).eq("id", leader_id).execute()
    return get_leader(leader_id, include_contact=True)


def set_leader_vetting(leader_id: str, vetting: dict, status: str) -> Optional[dict]:
    """Persist a vetting result onto a leader and update vetting_status."""
    db = _db()
    rows = (db.table("people_profiles").select("source_metadata")
            .eq("id", leader_id).eq("organization_id", MARKETPLACE_ORG_ID).execute().data)
    if not rows:
        return None
    meta = dict(rows[0].get("source_metadata") or {})
    meta["vetting"] = vetting
    meta["vetting_status"] = status
    meta["updated_at"] = _now()
    db.table("people_profiles").update({"source_metadata": meta}).eq("id", leader_id).execute()
    return get_leader(leader_id)


def record_vetting_attempt(leader_id: str) -> int:
    """Increment and return the persisted vetting-attempt counter (farming guard backstop)."""
    db = _db()
    rows = (db.table("people_profiles").select("source_metadata")
            .eq("id", leader_id).eq("organization_id", MARKETPLACE_ORG_ID).execute().data)
    if not rows:
        return 0
    meta = dict(rows[0].get("source_metadata") or {})
    count = int(meta.get("vetting_attempts") or 0) + 1
    meta["vetting_attempts"] = count
    meta["last_vetting_at"] = _now()
    db.table("people_profiles").update({"source_metadata": meta}).eq("id", leader_id).execute()
    return count


def delete_leader(leader_id: str) -> bool:
    """GDPR erasure: delete the leader profile row entirely."""
    db = _db()
    rows = (db.table("people_profiles").select("id")
            .eq("id", leader_id).eq("organization_id", MARKETPLACE_ORG_ID).execute().data)
    if not rows:
        return False
    db.table("people_profiles").delete().eq("id", leader_id).execute()
    return True


def export_leader(leader_id: str) -> Optional[dict]:
    """GDPR access: assemble everything held about a leader."""
    leader = get_leader(leader_id, include_contact=True)
    if not leader:
        return None
    intros = list_introductions_for_leader(leader_id, reveal_company_contact=True)
    return {
        "profile": leader,
        "vetting": leader.get("vetting"),
        "introductions": intros,
        "exported_at": _now(),
        "notice": ("This is the complete set of data ainm Marketplace holds about "
                   "your leader profile. To erase it, use the delete endpoint or "
                   "email compliance@ainm.ai."),
    }


# ── Opportunities + companies (opportunities) ────────────────────────────────

def _opp_from_row(row: dict) -> dict:
    meta = row.get("metadata") or {}
    company = meta.get("company") or {}
    return {
        "id": row.get("id"),
        "title": row.get("title") or "",
        "description": row.get("description") or "",
        "location": row.get("location") or "",
        "commitment_type": meta.get("engagement") or row.get("commitment_type") or "",
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
                       opp_id: Optional[str] = None,
                       org_id: Optional[str] = None,
                       created_by_user_id: Optional[str] = None) -> dict:
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
        "created_by_user_id": created_by_user_id or _system_actor() or MARKETPLACE_ORG_ID,
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
                     "engagement": commitment_type, "posted_by_org": org_id},
    }
    existing = db.table("opportunities").select("id").eq("id", row_id).execute().data
    if existing:
        db.table("opportunities").update(row).eq("id", row_id).execute()
    else:
        db.table("opportunities").insert(row).execute()
    return _opp_from_row(row)


# ── Company profiles (activity_log, entity_type='placement') ─────────────────
# A company profile is what leaders see when a company asks for an intro. It is
# owned by the buyer's org (one profile per org), stored namespaced so it never
# collides with the console.

def _company_from_row(row: dict) -> dict:
    meta = row.get("metadata") or {}
    prof = meta.get("company") or {}
    return {
        "id": prof.get("id") or row.get("organization_id"),
        "org_id": row.get("organization_id"),
        "name": prof.get("name") or "",
        "sector": prof.get("sector") or "",
        "size": prof.get("size") or "",
        "location": prof.get("location") or "",
        "website": prof.get("website") or "",
        "description": prof.get("description") or "",
        "contact_name": prof.get("contact_name") or "",
        "contact_email": prof.get("contact_email") or "",
        "created_at": row.get("created_at"),
        "updated_at": meta.get("updated_at") or row.get("created_at"),
    }


def get_company_profile(org_id: str) -> Optional[dict]:
    """Return the marketplace company profile owned by an org, if any."""
    if not org_id:
        return None
    db = _db()
    rows = (db.table("activity_log").select("*")
            .eq("organization_id", org_id)
            .eq("activity_type", COMPANY_ACTIVITY_TYPE)
            .order("created_at", desc=True).limit(1).execute().data) or []
    rows = [r for r in rows if (r.get("metadata") or {}).get("marketplace")]
    return _company_from_row(rows[0]) if rows else None


def upsert_company_profile(*, org_id: str, actor_id: str, **fields) -> dict:
    """Create or update the caller org's company profile (one per org)."""
    db = _db()
    existing = (db.table("activity_log").select("*")
                .eq("organization_id", org_id)
                .eq("activity_type", COMPANY_ACTIVITY_TYPE)
                .limit(1).execute().data) or []
    existing = [r for r in existing if (r.get("metadata") or {}).get("marketplace")]
    company = {k: v for k, v in fields.items() if v is not None}
    company.setdefault("id", str(uuid.uuid5(uuid.NAMESPACE_DNS, f"mkt-company:{org_id}")))
    company["org_id"] = org_id
    meta = {"marketplace": True, "kind": "company_profile", "company": company,
            "updated_at": _now()}
    if existing:
        row = existing[0]
        db.table("activity_log").update({"metadata": meta}).eq("id", row["id"]).execute()
        return _company_from_row({**row, "metadata": meta})
    row_id = str(uuid.uuid4())
    row = {
        "id": row_id,
        "organization_id": org_id,
        "entity_type": INTRO_ENTITY_TYPE,
        "entity_id": org_id,
        "activity_type": COMPANY_ACTIVITY_TYPE,
        "actor_id": actor_id,
        "summary": f"Marketplace company profile: {company.get('name', '')}",
        "metadata": meta,
    }
    db.table("activity_log").insert(row).execute()
    return _company_from_row(row)


def list_companies() -> list[dict]:
    """Distinct demand-side companies (derived from opportunities + real profiles)."""
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


def _intro_from_row(row: dict, *, viewer_org_id: Optional[str] = None,
                    viewer_is_leader: bool = False) -> dict:
    """Serialize an introduction.

    Contact reveal rules:
    - The requesting company sees the leader's contact only once the leader has
      accepted (contact_revealed / status in CONTACT_REVEALED_STATES).
    - The leader (viewer_is_leader) always sees who is asking (the company's
      contact) so they can decide.
    """
    meta = row.get("metadata") or {}
    status = meta.get("status") or "requested"
    contact_revealed = bool(meta.get("contact_revealed")) or status in CONTACT_REVEALED_STATES
    out = {
        "id": row.get("id"),
        "org_id": row.get("organization_id"),
        "leader_id": meta.get("leader_id"),
        "leader_name": meta.get("leader_name"),
        "company": meta.get("company") or {},
        "opportunity_id": meta.get("opportunity_id"),
        "opportunity_title": meta.get("opportunity_title"),
        "status": status,
        "leader_response": meta.get("leader_response") or ("accepted" if status in CONTACT_REVEALED_STATES
                                                           else "declined" if status == "declined" else "pending"),
        "message": meta.get("message") or "",
        "first_year_comp": meta.get("first_year_comp"),
        "placement_fee_pct": meta.get("placement_fee_pct", DEFAULT_PLACEMENT_FEE_PCT),
        "placement_fee_amount": meta.get("placement_fee_amount"),
        "hired": bool(meta.get("hired")),
        "outcome": meta.get("outcome"),
        "contact_revealed": contact_revealed,
        "requested_by": row.get("actor_id"),
        "created_at": row.get("created_at"),
        "updated_at": meta.get("updated_at") or row.get("created_at"),
    }
    # Reveal the leader's contact to the requesting company after acceptance.
    is_requesting_company = viewer_org_id is not None and viewer_org_id == row.get("organization_id")
    if is_requesting_company and contact_revealed:
        out["leader_contact"] = meta.get("leader_contact") or {}
    # The leader always sees who requested (company contact).
    if viewer_is_leader:
        out["requester_contact"] = meta.get("requester_contact") or {}
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
    from services.marketplace.constants import CONTACT_REVEALED_STATES as _crs
    leader_response = "accepted" if status in _crs else "declined" if status == "declined" else "pending"
    meta = {
        "marketplace": True,
        "kind": "introduction",
        "leader_id": leader_id,
        "leader_name": leader_name,
        "company": company,
        "opportunity_id": opportunity_id,
        "opportunity_title": opportunity_title,
        "status": status,
        "leader_response": leader_response,
        "message": message,
        "first_year_comp": first_year_comp,
        "placement_fee_pct": fee_pct,
        "placement_fee_amount": fee_amount,
        "hired": hired,
        "contact_revealed": status in _crs,
        "requester_contact": requester_contact or {},
        "leader_contact": leader_contact or {},
        "updated_at": created_at or _now(),
    }
    row = {
        "id": row_id,
        "organization_id": org_id,
        "entity_type": INTRO_ENTITY_TYPE,
        "entity_id": leader_id,
        "activity_type": INTRO_ACTIVITY_TYPE,
        "actor_id": actor_id,
        "summary": f"Introduction requested: {leader_name}"
                   + (f" → {company.get('name')}" if company.get("name") else ""),
        "metadata": meta,
    }
    insert = dict(row)
    if created_at:
        insert["created_at"] = created_at
    db.table("activity_log").insert(insert).execute()
    return _intro_from_row(row, viewer_org_id=org_id)


def _all_intro_rows(limit: int = 500) -> list[dict]:
    db = _db()
    rows = (db.table("activity_log").select("*")
            .eq("entity_type", INTRO_ENTITY_TYPE)
            .eq("activity_type", INTRO_ACTIVITY_TYPE)
            .order("created_at", desc=True).limit(limit).execute().data) or []
    return [r for r in rows if (r.get("metadata") or {}).get("marketplace")]


def list_introductions(*, org_id: Optional[str] = None, limit: int = 500) -> list[dict]:
    """List marketplace introductions.

    Passing org_id scopes to a single buyer org (tenant isolation for the
    company-facing 'my introductions' view). Omitting org_id returns the whole
    pipeline (operator/admin view).
    """
    rows = _all_intro_rows(limit=limit)
    if org_id:
        rows = [r for r in rows if r.get("organization_id") == org_id]
    return [_intro_from_row(r, viewer_org_id=org_id) for r in rows]


def list_introductions_for_leader(leader_id: str, *, limit: int = 500,
                                  reveal_company_contact: bool = True) -> list[dict]:
    """Introductions requested TO a given leader (the leader's inbox)."""
    rows = _all_intro_rows(limit=limit)
    rows = [r for r in rows if (r.get("metadata") or {}).get("leader_id") == leader_id]
    return [_intro_from_row(r, viewer_is_leader=reveal_company_contact) for r in rows]


def get_introduction(intro_id: str, *, viewer_org_id: Optional[str] = None,
                     viewer_is_leader: bool = False) -> Optional[dict]:
    db = _db()
    rows = db.table("activity_log").select("*").eq("id", intro_id).execute().data
    if not rows:
        return None
    return _intro_from_row(rows[0], viewer_org_id=viewer_org_id, viewer_is_leader=viewer_is_leader)


def _get_intro_row(intro_id: str) -> Optional[dict]:
    db = _db()
    rows = db.table("activity_log").select("*").eq("id", intro_id).execute().data
    return rows[0] if rows else None


def respond_to_introduction(intro_id: str, *, leader_id: str, decision: str,
                            leader_contact: Optional[dict] = None) -> Optional[dict]:
    """Leader accepts or declines an introduction request.

    On accept: status→accepted, contact_revealed=True, and the leader's contact
    is snapshotted onto the intro so the requesting company can reach them. On
    decline: status→declined, no contact revealed.
    Returns None if the intro doesn't exist or isn't addressed to this leader.
    """
    db = _db()
    row = _get_intro_row(intro_id)
    if not row:
        return None
    meta = dict(row.get("metadata") or {})
    if meta.get("leader_id") != leader_id:
        return None  # not this leader's request to answer
    if decision == "accepted":
        meta["leader_response"] = "accepted"
        meta["status"] = "accepted"
        meta["contact_revealed"] = True
        if leader_contact:
            meta["leader_contact"] = leader_contact
    else:
        meta["leader_response"] = "declined"
        meta["status"] = "declined"
        meta["contact_revealed"] = False
    meta["updated_at"] = _now()
    db.table("activity_log").update({"metadata": meta}).eq("id", intro_id).execute()
    return _intro_from_row({**row, "metadata": meta}, viewer_is_leader=True)


def update_introduction(intro_id: str, *, status: Optional[str] = None,
                        hired: Optional[bool] = None,
                        first_year_comp: Optional[float] = None,
                        fee_pct: Optional[float] = None,
                        outcome: Optional[str] = None,
                        viewer_org_id: Optional[str] = None) -> Optional[dict]:
    db = _db()
    row = _get_intro_row(intro_id)
    if not row:
        return None
    meta = dict(row.get("metadata") or {})
    if status is not None:
        meta["status"] = status
    if hired is not None:
        meta["hired"] = hired
        if hired and meta.get("status") not in ("hired",):
            meta["status"] = "hired"
    if outcome is not None:
        meta["outcome"] = outcome
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
    return _intro_from_row({**row, "metadata": meta}, viewer_org_id=viewer_org_id or row.get("organization_id"))


# ── Teardown (idempotent re-seed support) ────────────────────────────────────

def purge_marketplace() -> dict:
    """Delete every marketplace-namespaced row. Used before a fresh seed."""
    db = _db()
    counts = {}
    intros = (db.table("activity_log").select("id")
              .eq("entity_type", INTRO_ENTITY_TYPE)
              .eq("activity_type", INTRO_ACTIVITY_TYPE).execute().data) or []
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
    # NOTE: real company profiles are owned by buyer orgs (not MARKETPLACE_ORG_ID)
    # and are intentionally NOT purged here — purge only resets the synthetic pool.
    return counts
