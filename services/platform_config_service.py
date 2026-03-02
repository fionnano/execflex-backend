"""
Helpers for reading/writing platform-wide configuration from Supabase.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional, Tuple

from config.clients import supabase_client


def _unwrap_bool(raw_value: Any, default: bool) -> bool:
    """Best-effort parse for bool config values stored as JSON."""
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, dict):
        for key in ("enabled", "value", "boolean"):
            candidate = raw_value.get(key)
            if isinstance(candidate, bool):
                return candidate
    if isinstance(raw_value, str):
        normalized = raw_value.strip().lower()
        if normalized in ("1", "true", "yes", "y", "on"):
            return True
        if normalized in ("0", "false", "no", "n", "off"):
            return False
    return default


def _unwrap_number(raw_value: Any, default: float) -> float:
    """Best-effort parse for numeric config values stored as JSON."""
    if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
        return float(raw_value)
    if isinstance(raw_value, dict):
        for key in ("value", "number", "threshold", "ms"):
            candidate = raw_value.get(key)
            if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
                return float(candidate)
            if isinstance(candidate, str):
                try:
                    return float(candidate.strip())
                except Exception:
                    continue
    if isinstance(raw_value, str):
        try:
            return float(raw_value.strip())
        except Exception:
            return default
    return default


def _unwrap_string(raw_value: Any, default: str) -> str:
    """Best-effort parse for string config values stored as JSON."""
    if isinstance(raw_value, str):
        return raw_value
    if isinstance(raw_value, dict):
        for key in ("value", "text", "prompt", "message"):
            candidate = raw_value.get(key)
            if isinstance(candidate, str):
                return candidate
    if raw_value is None:
        return default
    try:
        return str(raw_value)
    except Exception:
        return default


def get_bool_config(key: str, default: bool = False) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Get bool config value from platform_config.

    Returns (value, updated_at, updated_by).
    """
    if not supabase_client:
        return default, None, None

    try:
        try:
            resp = (
                supabase_client.table("platform_config")
                .select("value, updated_at, updated_by")
                .eq("key", key)
                .limit(1)
                .execute()
            )
        except Exception:
            # Backward compatibility for environments where updated_by isn't migrated yet.
            resp = (
                supabase_client.table("platform_config")
                .select("value, updated_at")
                .eq("key", key)
                .limit(1)
                .execute()
            )
        if not resp.data:
            return default, None, None

        row = resp.data[0] or {}
        value = _unwrap_bool(row.get("value"), default)
        return value, row.get("updated_at"), row.get("updated_by")
    except Exception as exc:
        print(f"Failed to read platform_config key={key}: {exc}", flush=True)
        return default, None, None


def set_bool_config(key: str, value: bool, updated_by: Optional[str] = None, description: Optional[str] = None) -> dict:
    """Upsert bool config value into platform_config."""
    if not supabase_client:
        raise RuntimeError("Supabase client is not configured")

    now_iso = datetime.now(timezone.utc).isoformat()
    payload = {
        "key": key,
        "value": bool(value),
        "updated_at": now_iso,
        "updated_by": updated_by,
    }
    if description:
        payload["description"] = description

    try:
        resp = (
            supabase_client.table("platform_config")
            .upsert(payload, on_conflict="key")
            .execute()
        )
    except Exception:
        # Backward compatibility for environments where updated_by isn't migrated yet.
        payload.pop("updated_by", None)
        resp = (
            supabase_client.table("platform_config")
            .upsert(payload, on_conflict="key")
            .execute()
        )
    if not resp.data:
        return payload
    return resp.data[0]


def get_number_config(key: str, default: float) -> Tuple[float, Optional[str], Optional[str]]:
    """
    Get numeric config value from platform_config.

    Returns (value, updated_at, updated_by).
    """
    if not supabase_client:
        return default, None, None

    try:
        try:
            resp = (
                supabase_client.table("platform_config")
                .select("value, updated_at, updated_by")
                .eq("key", key)
                .limit(1)
                .execute()
            )
        except Exception:
            resp = (
                supabase_client.table("platform_config")
                .select("value, updated_at")
                .eq("key", key)
                .limit(1)
                .execute()
            )
        if not resp.data:
            return default, None, None

        row = resp.data[0] or {}
        value = _unwrap_number(row.get("value"), default)
        return value, row.get("updated_at"), row.get("updated_by")
    except Exception as exc:
        print(f"Failed to read numeric platform_config key={key}: {exc}", flush=True)
        return default, None, None


def set_number_config(key: str, value: float, updated_by: Optional[str] = None, description: Optional[str] = None) -> dict:
    """Upsert numeric config value into platform_config."""
    if not supabase_client:
        raise RuntimeError("Supabase client is not configured")

    now_iso = datetime.now(timezone.utc).isoformat()
    payload = {
        "key": key,
        "value": float(value),
        "updated_at": now_iso,
        "updated_by": updated_by,
    }
    if description:
        payload["description"] = description

    try:
        resp = (
            supabase_client.table("platform_config")
            .upsert(payload, on_conflict="key")
            .execute()
        )
    except Exception:
        payload.pop("updated_by", None)
        resp = (
            supabase_client.table("platform_config")
            .upsert(payload, on_conflict="key")
            .execute()
        )
    if not resp.data:
        return payload
    return resp.data[0]


def get_string_config(key: str, default: str) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Get string config value from platform_config.

    Returns (value, updated_at, updated_by).
    """
    if not supabase_client:
        return default, None, None

    try:
        try:
            resp = (
                supabase_client.table("platform_config")
                .select("value, updated_at, updated_by")
                .eq("key", key)
                .limit(1)
                .execute()
            )
        except Exception:
            resp = (
                supabase_client.table("platform_config")
                .select("value, updated_at")
                .eq("key", key)
                .limit(1)
                .execute()
            )
        if not resp.data:
            return default, None, None

        row = resp.data[0] or {}
        value = _unwrap_string(row.get("value"), default)
        return value, row.get("updated_at"), row.get("updated_by")
    except Exception as exc:
        print(f"Failed to read string platform_config key={key}: {exc}", flush=True)
        return default, None, None


def set_string_config(key: str, value: str, updated_by: Optional[str] = None, description: Optional[str] = None) -> dict:
    """Upsert string config value into platform_config."""
    if not supabase_client:
        raise RuntimeError("Supabase client is not configured")

    now_iso = datetime.now(timezone.utc).isoformat()
    payload = {
        "key": key,
        "value": str(value),
        "updated_at": now_iso,
        "updated_by": updated_by,
    }
    if description:
        payload["description"] = description

    try:
        resp = (
            supabase_client.table("platform_config")
            .upsert(payload, on_conflict="key")
            .execute()
        )
    except Exception:
        payload.pop("updated_by", None)
        resp = (
            supabase_client.table("platform_config")
            .upsert(payload, on_conflict="key")
            .execute()
        )
    if not resp.data:
        return payload
    return resp.data[0]
