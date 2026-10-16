"""
Global do-not-contact list (GDPR-E-004, Art. 21).

A "not interested" reply, an erasure request or an objection used to set a
per-thread status only, so the same person could be sourced, enriched and
e-mailed again for the next role. Every outreach and call path now asks
``is_suppressed`` first, and the paths that learn of an objection call
``suppress``.

Backed by the ``contact_suppressions`` table
(supabase/migrations/20261016_add_contact_suppressions.sql). Until that
migration is applied the lookups log an error and return False, so the
rest of the pipeline keeps working; the PR notes say to apply it first.
"""
from datetime import datetime, timezone
from typing import Dict, List, Optional

from utils.log_redaction import mask_email, mask_phone

TABLE = "contact_suppressions"


def normalize_email(value: Optional[str]) -> Optional[str]:
    if not value or not isinstance(value, str):
        return None
    value = value.strip().lower()
    return value if "@" in value else None


def normalize_phone(value: Optional[str]) -> Optional[str]:
    if not value or not isinstance(value, str):
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) < 7:
        return None
    return "+" + digits


def _client():
    from config.clients import supabase_client
    return supabase_client


def is_suppressed(email: Optional[str] = None, phone: Optional[str] = None) -> bool:
    """True when either contact value is on the do-not-contact list."""
    sb = _client()
    if not sb:
        return False
    checks = []
    norm_email = normalize_email(email)
    norm_phone = normalize_phone(phone)
    if norm_email:
        checks.append(("email", norm_email))
    if norm_phone:
        checks.append(("phone", norm_phone))
    for column, value in checks:
        try:
            resp = sb.table(TABLE).select("id").eq(column, value).limit(1).execute()
        except Exception as e:
            print(f"[SUPPRESSION] lookup failed ({column}): {e}", flush=True)
            continue
        if resp.data:
            return True
    return False


def suppress(
    email: Optional[str] = None,
    phone: Optional[str] = None,
    reason: str = "objection",
    source: str = "unknown",
    profile_id: Optional[str] = None,
) -> Dict:
    """Add contact values to the do-not-contact list. Never raises."""
    sb = _client()
    norm_email = normalize_email(email)
    norm_phone = normalize_phone(phone)
    if not sb or not (norm_email or norm_phone):
        return {"added": False, "reason": "nothing_to_suppress"}
    row = {
        "email": norm_email,
        "phone": norm_phone,
        "reason": reason,
        "source": source,
        "profile_id": profile_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        sb.table(TABLE).insert(row).execute()
        print(
            f"[SUPPRESSION] added email={mask_email(norm_email)} "
            f"phone={mask_phone(norm_phone)} reason={reason} source={source}",
            flush=True,
        )
        return {"added": True}
    except Exception as e:
        print(f"[SUPPRESSION] insert failed: {e}", flush=True)
        return {"added": False, "reason": str(e)}


def filter_suppressed(rows: List[Dict], email_key: str = "email", phone_key: str = "phone") -> List[Dict]:
    """Drop rows whose email/phone is suppressed."""
    return [r for r in rows if not is_suppressed(r.get(email_key), r.get(phone_key))]
