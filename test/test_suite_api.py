"""Suite (unified shell) entitlements — real-path route tests."""
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import jwt as pyjwt

ORG_A = str(uuid.uuid4())
USER_A = str(uuid.uuid4())


def token(org_id=ORG_A, user_id=USER_A, role="owner"):
    from config.app_config import SUPABASE_JWT_SECRET
    payload = {"sub": user_id, "aud": "authenticated",
               "app_metadata": {"org_id": org_id, "role": role}}
    return pyjwt.encode(payload, SUPABASE_JWT_SECRET or "test-secret", algorithm="HS256")


def auth(org_id=ORG_A):
    return {"Authorization": f"Bearer {token(org_id=org_id)}"}


@pytest.fixture
def client():
    from server import app
    app.config["TESTING"] = True
    return app.test_client()


def test_requires_auth(client):
    assert client.get("/api/v1/suite/modules").status_code == 401


def test_returns_internal_and_external_modules(client):
    r = client.get("/api/v1/suite/modules", headers=auth())
    assert r.status_code == 200
    data = r.get_json()["data"]
    keys = {m["key"] for m in data["modules"]}
    # Internal (one-login) + external (separate sign-in) both present by default.
    assert {"search", "marketplace", "aiact"}.issubset(keys)
    assert {"hr", "transparency"}.issubset(keys)
    internal = {m["key"]: m for m in data["modules"] if m["internal"]}
    assert internal["marketplace"]["path"] == "/marketplace"
    assert internal["aiact"]["path"] == "/ai-act"
    external = {m["key"]: m for m in data["modules"] if m["external"]}
    assert external["hr"]["separate_login"] is True
    assert external["hr"]["url"].startswith("https://")


def test_org_restriction_via_env(client, monkeypatch):
    monkeypatch.setenv("SUITE_ORG_MODULES", f'{{"{ORG_A}": ["marketplace", "aiact"]}}')
    r = client.get("/api/v1/suite/modules", headers=auth(org_id=ORG_A))
    keys = {m["key"] for m in r.get_json()["data"]["modules"]}
    assert keys == {"marketplace", "aiact"}
    # A different org still gets the default full set.
    other = str(uuid.uuid4())
    r2 = client.get("/api/v1/suite/modules", headers=auth(org_id=other))
    assert len(r2.get_json()["data"]["modules"]) == 5


def test_all_flag_returns_locked(client, monkeypatch):
    monkeypatch.setenv("SUITE_DEFAULT_MODULES", "marketplace")
    r = client.get("/api/v1/suite/modules?all=1", headers=auth(org_id=str(uuid.uuid4())))
    mods = {m["key"]: m for m in r.get_json()["data"]["modules"]}
    assert mods["marketplace"]["entitled"] is True
    assert mods["search"]["entitled"] is False  # locked but listed
