"""AI Act assessment persistence — dedicated table (graduated from the namespace).

See DECISIONS.md D-18/D-19. Assessments now live in the dedicated
`aiact_assessments` table (applied to prod via AI_ACT_MIGRATION.sql), owned by
the creating org (tenant key = organization_id). Every read is org-scoped, so a
company only ever sees its own assessments. GDPR export + delete supported.

Public function signatures and serialized shapes are UNCHANGED from the
namespaced version, so routes, the frontend, and tests are unaffected. Defensive:
the Supabase client is fetched lazily.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

TBL = "aiact_assessments"


def _db():
    from config.clients import supabase_client
    return supabase_client


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _from_row(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "org_id": row.get("organization_id"),
        "system_name": row.get("system_name") or "",
        "status": row.get("status") or "draft",
        "answers": row.get("answers") or {},
        "result": row.get("result"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at") or row.get("created_at"),
    }


def create_assessment(*, org_id: str, actor_id: str, system_name: str,
                      answers: Optional[dict] = None) -> dict:
    db = _db()
    row_id = str(uuid.uuid4())
    row = {
        "id": row_id,
        "organization_id": org_id,
        "created_by": actor_id,
        "system_name": system_name,
        "status": "draft",
        "answers": answers or {},
        "result": None,
        "updated_at": _now(),
    }
    db.table(TBL).insert(row).execute()
    return _from_row(row)


def get_assessment(assessment_id: str, *, org_id: str) -> Optional[dict]:
    """Tenant-scoped read — only returns the row if it belongs to org_id."""
    db = _db()
    rows = (db.table(TBL).select("*")
            .eq("id", assessment_id)
            .eq("organization_id", org_id).execute().data) or []
    return _from_row(rows[0]) if rows else None


def update_assessment(assessment_id: str, *, org_id: str,
                      answers: Optional[dict] = None,
                      result: Optional[dict] = None,
                      status: Optional[str] = None,
                      system_name: Optional[str] = None) -> Optional[dict]:
    db = _db()
    rows = (db.table(TBL).select("*")
            .eq("id", assessment_id)
            .eq("organization_id", org_id).execute().data) or []
    if not rows:
        return None
    row = rows[0]
    upd: dict[str, Any] = {"updated_at": _now()}
    if answers is not None:
        upd["answers"] = answers
    if result is not None:
        upd["result"] = result
        upd["risk_classification"] = result.get("risk_classification")
        upd["readiness_score"] = result.get("readiness_score")
        upd["ai_generated"] = bool(result.get("ai_generated"))
    if status is not None:
        upd["status"] = status
    if system_name is not None:
        upd["system_name"] = system_name
    db.table(TBL).update(upd).eq("id", assessment_id).execute()
    return _from_row({**row, **upd})


def list_assessments(*, org_id: str, limit: int = 100) -> list[dict]:
    db = _db()
    rows = (db.table(TBL).select("*")
            .eq("organization_id", org_id)
            .order("created_at", desc=True).limit(limit).execute().data) or []
    return [_from_row(r) for r in rows]


def delete_assessment(assessment_id: str, *, org_id: str) -> bool:
    """GDPR erase — tenant-scoped."""
    db = _db()
    rows = (db.table(TBL).select("id")
            .eq("id", assessment_id)
            .eq("organization_id", org_id).execute().data) or []
    if not rows:
        return False
    db.table(TBL).delete().eq("id", assessment_id).execute()
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
