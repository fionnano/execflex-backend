"""Suite module registry + entitlement resolver (config-driven).

The registry is static metadata. Entitlements are resolved per org from config:
  1. SUITE_ORG_MODULES  — JSON {org_id: [module_keys]} for per-org restriction.
  2. SUITE_DEFAULT_MODULES — comma-separated keys, the default entitled set for
     any org not named in SUITE_ORG_MODULES (defaults to ALL modules).
External module URLs are overridable via env so links are never hard-coded to a
domain that might change.

Nothing here touches a product's internals — it only decides which module cards
a user sees and where each one points.
"""
from __future__ import annotations

import json
import os

# key → metadata. `internal` modules live in the execflex.ai SPA and share the
# same Supabase login (seamless). `external` modules are separate apps with their
# own sign-in (shell-linked).
_REGISTRY: list[dict] = [
    {
        "key": "search",
        "label": "ainm Search",
        "tagline": "AI recruiting console",
        "description": "Source, screen, and manage candidates with Aidan, the AI recruiter.",
        "icon": "search",
        "internal": True,
        "path": "/console",
        "env_url": None,
        "default_url": None,
    },
    {
        "key": "marketplace",
        "label": "ainm Marketplace",
        "tagline": "Vetted AI & data leaders",
        "description": "A curated marketplace of independently vetted AI and data leaders.",
        "icon": "users",
        "internal": True,
        "path": "/marketplace",
        "env_url": None,
        "default_url": None,
    },
    {
        "key": "aiact",
        "label": "EU AI Act Check",
        "tagline": "AI Act readiness",
        "description": "Assess your AI use for EU AI Act risk — obligations, gaps, readiness.",
        "icon": "shield-check",
        "internal": True,
        "path": "/ai-act",
        "env_url": None,
        "default_url": None,
    },
    {
        "key": "hr",
        "label": "ainm HR",
        "tagline": "AI-native HR platform",
        "description": "Ireland's AI-native HR platform — Irish employment law, hire to retire.",
        "icon": "briefcase",
        "internal": False,
        "path": None,
        "env_url": "SUITE_URL_HR",
        "default_url": "https://ainm.ai",
    },
    {
        "key": "transparency",
        "label": "Transparency",
        "tagline": "Pay-equity reporting",
        "description": "Pay equity analytics and reporting for the EU pay-transparency directive.",
        "icon": "bar-chart",
        "internal": False,
        "path": None,
        "env_url": "SUITE_URL_TRANSPARENCY",
        "default_url": "https://transparency.ainm.ai",
    },
]

_ALL_KEYS = [m["key"] for m in _REGISTRY]


def _default_entitled() -> list[str]:
    raw = os.environ.get("SUITE_DEFAULT_MODULES", "").strip()
    if not raw:
        return list(_ALL_KEYS)
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    return [k for k in keys if k in _ALL_KEYS] or list(_ALL_KEYS)


def _org_overrides() -> dict:
    raw = os.environ.get("SUITE_ORG_MODULES", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _module_url(m: dict) -> str | None:
    if m["internal"]:
        return None
    return os.environ.get(m["env_url"] or "", "") or m["default_url"]


def _serialize(m: dict, entitled: bool) -> dict:
    return {
        "key": m["key"],
        "label": m["label"],
        "tagline": m["tagline"],
        "description": m["description"],
        "icon": m["icon"],
        "internal": m["internal"],
        "external": not m["internal"],
        "separate_login": not m["internal"],
        "path": m["path"],
        "url": _module_url(m),
        "entitled": entitled,
    }


def entitled_keys(org_id: str) -> list[str]:
    """Resolve the entitled module keys for an org (config-driven)."""
    overrides = _org_overrides()
    if org_id in overrides and isinstance(overrides[org_id], list):
        return [k for k in overrides[org_id] if k in _ALL_KEYS]
    return _default_entitled()


def resolve_modules(org_id: str, *, include_locked: bool = False) -> list[dict]:
    """Return the module cards for an org.

    By default returns only entitled modules (the spec's "show only what the
    user/org is entitled to"). include_locked=True returns the full registry with
    an `entitled` flag (useful for an admin/upsell view).
    """
    allowed = set(entitled_keys(org_id))
    out = []
    for m in _REGISTRY:
        ent = m["key"] in allowed
        if ent or include_locked:
            out.append(_serialize(m, ent))
    return out


def all_module_keys() -> list[str]:
    return list(_ALL_KEYS)
