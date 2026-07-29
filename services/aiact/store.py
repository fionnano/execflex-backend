"""AI Act assessment persistence — namespaced on activity_log.

Every assessment is owned by the org that created it (tenant key = org_id) and is
stored in activity_log (entity_type='client', activity_type='ai_act_assessment',
metadata.aiact=True). All reads are org-scoped, so a company only ever sees its
own assessments. No new prod DDL (see AI_ACT_MIGRATION.sql for the graduation
path). Defensive: the Supabase client is fetched lazily.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from services.aiact.constants import AIACT_ENTITY_TYPE, AIACT_ACTIVITY_TYPE


def _db():
    from config.clients import supabase_client
    return supabase_client


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _from_row(row: dict) -> dict:
    meta = row.get("metadata") or {}
    return {
        "id": row.get("id"),
        "org_id": row.get("organization_id"),
        "system_name": meta.get("system_name") or "",
        "status": meta.get("status") or "draft",
        "answers": meta.get("answers") or {},
        "result": meta.get("result"),
        "created_at": meta.get("created_at") or row.get("created_at"),
        "updated_at": meta.get("updated_at") or row.get("created_at"),
    }


def create_assessment(*, org_id: str, actor_id: str, system_name: str,
                      answers: Optional[dict] = None) -> dict:
    db = _db()
    row_id = str(uuid.uuid4())
    now = _now()
    meta = {
        "aiact": True,
        "system_name": system_name,
        "status": "draft",
        "answers": answers or {},
        "result": None,
        "created_at": now,
        "updated_at": now,
    }
    row = {
        "id": row_id,
        "organization_id": org_id,
        "entity_type": AIACT_ENTITY_TYPE,
        "entity_id": row_id,
        "activity_type": AIACT_ACTIVITY_TYPE,
        "actor_id": actor_id,
        "summary": f"AI Act assessment: {system_name}",
        "metadata": meta,
    }
    db.table("activity_log").insert(row).execute()
    return _from_row(row)


def get_assessment(assessment_id: str, *, org_id: str) -> Optional[dict]:
    """Tenant-scoped read — only returns the row if it belongs to org_id."""
    db = _db()
    rows = (db.table("activity_log").select("*")
            .eq("id", assessment_id)
            .eq("organization_id", org_id).execute().data) or []
    rows = [r for r in rows if (r.get("metadata") or {}).get("aiact")]
    return _from_row(rows[0]) if rows else None


def _get_row(assessment_id: str, org_id: str) -> Optional[dict]:
    db = _db()
    rows = (db.table("activity_log").select("*")
            .eq("id", assessment_id)
            .eq("organization_id", org_id).execute().data) or []
    rows = [r for r in rows if (r.get("metadata") or {}).get("aiact")]
    return rows[0] if rows else None


def update_assessment(assessment_id: str, *, org_id: str,
                      answers: Optional[dict] = None,
                      result: Optional[dict] = None,
                      status: Optional[str] = None,
                      system_name: Optional[str] = None) -> Optional[dict]:
    row = _get_row(assessment_id, org_id)
    if not row:
        return None
    meta = dict(row.get("metadata") or {})
    if answers is not None:
        meta["answers"] = answers
    if result is not None:
        meta["result"] = result
    if status is not None:
        meta["status"] = status
    if system_name is not None:
        meta["system_name"] = system_name
    meta["updated_at"] = _now()
    _db().table("activity_log").update({"metadata": meta}).eq("id", assessment_id).execute()
    return _from_row({**row, "metadata": meta})


def list_assessments(*, org_id: str, limit: int = 100) -> list[dict]:
    db = _db()
    rows = (db.table("activity_log").select("*")
            .eq("organization_id", org_id)
            .eq("activity_type", AIACT_ACTIVITY_TYPE)
            .order("created_at", desc=True).limit(limit).execute().data) or []
    rows = [r for r in rows if (r.get("metadata") or {}).get("aiact")]
    return [_from_row(r) for r in rows]


def delete_assessment(assessment_id: str, *, org_id: str) -> bool:
    """GDPR erase — tenant-scoped."""
    row = _get_row(assessment_id, org_id)
    if not row:
        return False
    _db().table("activity_log").delete().eq("id", assessment_id).execute()
    return True


def export_assessment(assessment_id: str, *, org_id: str) -> Optional[dict]:
    """GDPR access — the full assessment record."""
    a = get_assessment(assessment_id, org_id=org_id)
    if not a:
        return None
    return {
        "assessment": a,
        "exported_at": _now(),
        "notice": ("This is the complete record ainm AI Act Check holds for this "
                   "assessment. To erase it, use the delete endpoint."),
    }
