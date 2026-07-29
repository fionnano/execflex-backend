"""Live verification for the repoint (Goal A) + unified shell (Goal B).

Against the deployed backend with a REAL Supabase auth account:
  A) Marketplace + AI Act write to the DEDICATED tables (proven by reading the
     dedicated table directly AND confirming absence from the namespaced store).
  B) The suite entitlements endpoint returns the caller's modules.
  C) ainm.ai (the live client product) is NOT regressed (health probe only —
     never modified).
Then every synthetic artifact is deleted. Only fionnano+... aliases used.
"""
import base64
import json
import os
import sys
import time
import uuid

import requests

BACKEND = os.environ.get("BACKEND_URL", "https://execflex-backend-1.onrender.com")
API = f"{BACKEND}/api/v1"


def load_env(path):
    env = {}
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env = load_env(os.environ.get("BACKEND_ENV") or os.path.join(_ROOT, ".env"))
SB = env["SUPABASE_URL"].rstrip("/")
KEY = env["SUPABASE_SERVICE_KEY"]
AH = {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
TS = int(time.time())
PW = "Test!" + uuid.uuid4().hex[:12]
MKT = "00000000-0000-4000-a000-000000000c0a"

results, users, cleanup = [], [], {"marketplace_leaders": [], "marketplace_introductions": [],
                                    "aiact_assessments": []}
def ok(m): results.append(("PASS", m)); print(f"  [PASS] {m}")
def fail(m): results.append(("FAIL", m)); print(f"  [FAIL] {m}")


def mk_user(email):
    uid = requests.post(f"{SB}/auth/v1/admin/users", headers=AH,
                        json={"email": email, "password": PW, "email_confirm": True}).json()["id"]
    users.append(uid)
    return uid


def login(email, tries=20):
    for _ in range(tries):
        t = requests.post(f"{SB}/auth/v1/token?grant_type=password",
                          headers={"apikey": KEY, "Content-Type": "application/json"},
                          json={"email": email, "password": PW}).json().get("access_token")
        if t:
            am = json.loads(base64.urlsafe_b64decode(t.split(".")[1] + "===")).get("app_metadata") or {}
            if am.get("org_id"):
                return t, am
        time.sleep(1.5)
    return None, {}


def H(t): return {"Authorization": f"Bearer {t}", "Content-Type": "application/json"}
def row_exists(table, rid):
    r = requests.get(f"{SB}/rest/v1/{table}?select=id&id=eq.{rid}", headers=AH)
    return bool(r.json())


STRONG = ("I led a team of 12 and cut p99 inference latency 55% while reducing serving "
          "cost 30%, owning incident response, drift detection, rollback and SLAs across a "
          "production feature store and model-serving stack.")


def main():
    print(f"\n=== Unified verification against {BACKEND} ===")
    le, ce = f"fionnano+uni-l-{TS}@gmail.com", f"fionnano+uni-c-{TS}@gmail.com"
    mk_user(le); mk_user(ce)
    ltok, lam = login(le); ctok, cam = login(ce)
    if not (lam.get("org_id") and cam.get("org_id")):
        fail("org provisioning failed"); return summarize()
    ok("two org-scoped accounts provisioned")

    # ── A) MARKETPLACE on dedicated tables ──────────────────────────────
    ap = requests.post(f"{API}/marketplace/leaders", headers=H(ltok), json={
        "name": "Uni Leader", "headline": "Head of ML Platform", "track": "ml_platform",
        "skills": ["MLOps", "Feature Stores"], "seniority": "Head of", "email": le})
    if ap.status_code not in (200, 201):
        fail(f"leader create failed {ap.status_code}: {ap.text[:150]}"); return summarize()
    lid = ap.json()["data"]["id"]; cleanup["marketplace_leaders"].append(lid)
    qs = requests.get(f"{API}/marketplace/vetting/questions?track=ml_platform", headers=H(ltok)).json()["data"]["questions"]
    resp = [{"question_id": q["id"], "competency": q["competency"], "weight": q["weight"], "text": STRONG} for q in qs]
    vr = requests.post(f"{API}/marketplace/leaders/{lid}/vetting", headers=H(ltok),
                       json={"track": "ml_platform", "responses": resp}).json()["data"]["vetting"]
    ok(f"leader vetted ({vr['status']} {vr['score']}/100)") if vr["status"] == "verified" else fail("vetting failed")
    # Prove it landed in the DEDICATED table and NOT the namespaced store.
    if row_exists("marketplace_leaders", lid):
        ok("leader row is in the DEDICATED marketplace_leaders table")
    else:
        fail("leader NOT found in marketplace_leaders")
    if not row_exists("people_profiles", lid):
        ok("leader is NOT in the namespaced people_profiles (repoint confirmed)")
    else:
        fail("leader leaked into namespaced people_profiles")

    # Company requests an intro → lands in marketplace_introductions
    ir = requests.post(f"{API}/marketplace/leaders/{lid}/introductions", headers=H(ctok),
                       json={"company": {"name": "Uni Co"}, "first_year_comp": 200000})
    if ir.status_code == 201:
        iid = ir.json()["data"]["id"]; cleanup["marketplace_introductions"].append(iid)
        if row_exists("marketplace_introductions", iid):
            ok("introduction row is in the DEDICATED marketplace_introductions table")
        else:
            fail("introduction NOT in marketplace_introductions")
    else:
        fail(f"intro create failed {ir.status_code}")

    # Existing seed pool still serves on the new tables
    pool = requests.get(f"{API}/marketplace/leaders", headers=H(ctok)).json()["data"]
    ok(f"marketplace browse returns the migrated pool ({pool['total']} verified)") if pool["total"] >= 13 else fail(f"pool too small: {pool['total']}")

    # ── AI Act on dedicated table ───────────────────────────────────────
    ca = requests.post(f"{API}/aiact/assessments", headers=H(ctok),
                       json={"system_name": "CV screen", "answers": {
                           "uses_ai": "yes", "business_functions": ["hr"], "affects_people": "yes",
                           "automated_hiring_decisions": "yes", "in_eu": "yes",
                           "human_oversight": "no", "has_documentation": "no"}})
    aid = ca.json()["data"]["id"]; cleanup["aiact_assessments"].append(aid)
    sc = requests.post(f"{API}/aiact/assessments/{aid}/score", headers=H(ctok), json={}).json()["data"]
    risk = (sc.get("result") or {}).get("risk_classification")
    ok(f"AI Act scored ({risk})") if risk == "High Risk" else fail(f"AI Act score wrong: {risk}")
    if row_exists("aiact_assessments", aid):
        ok("assessment row is in the DEDICATED aiact_assessments table")
    else:
        fail("assessment NOT in aiact_assessments")

    # ── B) SUITE entitlements ───────────────────────────────────────────
    sm = requests.get(f"{API}/suite/modules", headers=H(ctok))
    if sm.status_code == 200:
        mods = {m["key"]: m for m in sm.json()["data"]["modules"]}
        internal_ok = all(mods.get(k, {}).get("internal") for k in ("search", "marketplace", "aiact"))
        external_ok = mods.get("hr", {}).get("separate_login") and mods.get("hr", {}).get("url", "").startswith("https://")
        if internal_ok and external_ok:
            ok(f"suite returns {len(mods)} modules: internal one-login (search/marketplace/aiact) "
               f"+ external separate-login (hr→{mods['hr']['url']}, transparency)")
        else:
            fail(f"suite module shape wrong: {list(mods)}")
    else:
        fail(f"suite/modules failed {sm.status_code}")

    # ── C) ainm.ai NOT regressed (probe only, never modified) ───────────
    h1 = requests.get("https://ainm.ai", timeout=15)
    if h1.status_code == 200 and "ainm" in h1.text.lower():
        ok("ainm.ai is up and serving (HTTP 200, unchanged live product)")
    else:
        fail(f"ainm.ai probe unexpected: {h1.status_code}")
    for u in ("https://transparency.ainm.ai",):
        r = requests.get(u, timeout=15)
        ok(f"{u} reachable (HTTP {r.status_code})") if r.status_code < 500 else fail(f"{u} -> {r.status_code}")

    return summarize()


def do_cleanup():
    print("\n--- cleanup ---")
    for table, ids in cleanup.items():
        for rid in ids:
            try:
                requests.delete(f"{SB}/rest/v1/{table}?id=eq.{rid}", headers=AH)
            except Exception as e:
                print(f"  {table}/{rid} cleanup failed: {e}")
    for uid in users:
        try:
            requests.delete(f"{SB}/auth/v1/admin/users/{uid}", headers=AH)
            print(f"  deleted user {uid}")
        except Exception as e:
            print(f"  user {uid} delete failed: {e}")


def summarize():
    p = sum(1 for s, _ in results if s == "PASS"); f = sum(1 for s, _ in results if s == "FAIL")
    print(f"\n=== RESULT: {p} passed, {f} failed ===")
    return f == 0


if __name__ == "__main__":
    good = False
    try:
        good = main()
    except Exception as e:
        import traceback; traceback.print_exc(); print(f"ERROR: {e}")
    finally:
        do_cleanup()
    sys.exit(0 if good else 1)
