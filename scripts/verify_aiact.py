"""Live end-to-end verification of the ainm AI Act Check against the deployed
backend, using REAL Supabase auth accounts (created via the admin API).

Creates two org accounts, runs a full assessment journey (questions → create →
score → result), asserts the classification / obligations / gaps / disclaimer,
checks a prohibited-practice case and tenant isolation and GDPR, then deletes
every synthetic artifact. No real company data; only fionnano+... aliases.
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
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env = load_env(os.environ.get("BACKEND_ENV") or os.path.join(_REPO_ROOT, ".env"))
SB_URL = env["SUPABASE_URL"].rstrip("/")
SB_KEY = env["SUPABASE_SERVICE_KEY"]
ADMIN_H = {"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}", "Content-Type": "application/json"}

TS = int(time.time())
PW = "Test!" + uuid.uuid4().hex[:12]
results = []
created_user_ids = []
cleanup_ids = []  # activity_log ids to delete


def ok(m): results.append(("PASS", m)); print(f"  [PASS] {m}")
def fail(m): results.append(("FAIL", m)); print(f"  [FAIL] {m}")


def admin_create_user(email):
    r = requests.post(f"{SB_URL}/auth/v1/admin/users", headers=ADMIN_H,
                      json={"email": email, "password": PW, "email_confirm": True})
    r.raise_for_status()
    uid = r.json()["id"]
    created_user_ids.append(uid)
    return uid


def login_with_org(email, tries=20):
    for _ in range(tries):
        t = requests.post(f"{SB_URL}/auth/v1/token?grant_type=password",
                          headers={"apikey": SB_KEY, "Content-Type": "application/json"},
                          json={"email": email, "password": PW}).json().get("access_token")
        if t:
            p = t.split(".")[1] + "==="
            am = (json.loads(base64.urlsafe_b64decode(p)) or {}).get("app_metadata") or {}
            if am.get("org_id"):
                return t, am
        time.sleep(1.5)
    return None, {}


def H(tok):
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


HIGH_RISK = {
    "system_name": "CV screening tool", "uses_ai": "yes", "business_functions": ["hr"],
    "affects_people": "yes", "automated_hiring_decisions": "yes", "in_eu": "yes",
    "human_oversight": "no", "data_governance": "no", "keeps_logs": "no",
    "has_documentation": "no", "candidates_informed": "no",
}


def wait_ready(tok, tries=18):
    """Confirm agentic-core v0.18.0 is loaded (questions returns 4 stages)."""
    for i in range(tries):
        r = requests.get(f"{API}/aiact/questions", headers=H(tok))
        if r.status_code == 200 and len(r.json()["data"].get("stages", [])) == 4:
            return r.json()["data"]
        print(f"  deploy probe {i}: questions → {r.status_code}")
        time.sleep(20)
    return None


def create_and_score(tok, answers, ai=False):
    c = requests.post(f"{API}/aiact/assessments", headers=H(tok),
                      json={"system_name": answers.get("system_name", "System"), "answers": answers})
    if c.status_code != 201:
        return None, c
    aid = c.json()["data"]["id"]
    cleanup_ids.append(aid)
    s = requests.post(f"{API}/aiact/assessments/{aid}/score", headers=H(tok),
                      json={"ai": ai})
    return aid, s


def main():
    print(f"\n=== AI Act Check live verification against {BACKEND} ===")
    e1 = f"fionnano+aiact-a-{TS}@gmail.com"
    e2 = f"fionnano+aiact-b-{TS}@gmail.com"
    u1 = admin_create_user(e1); u2 = admin_create_user(e2)
    print(f"Created users {u1} / {u2}")

    tok1, am1 = login_with_org(e1)
    tok2, am2 = login_with_org(e2)
    if not (am1.get("org_id") and am2.get("org_id")):
        fail("org provisioning did not set org_id in JWT"); return summarize()
    ok(f"two org-scoped accounts (org A={am1['org_id'][:8]}…, org B={am2['org_id'][:8]}…)")

    qs = wait_ready(tok1)
    if not qs:
        fail("backend not serving the v0.18.0 question set (deploy not ready)"); return summarize()
    ok(f"question set live: {len(qs['stages'])} stages, disclaimer present "
       f"({'not legal advice' in qs['disclaimer'].lower()})")

    # High-risk hiring assessment (deterministic)
    aid, s = create_and_score(tok1, HIGH_RISK)
    if not s or s.status_code != 200:
        fail(f"score failed: {s.status_code if s else 'n/a'} {getattr(s, 'text', '')[:200]}")
        return summarize()
    res = s.json()["data"]["result"]
    print(f"    risk={res['risk_classification']} score={res['readiness_score']} "
          f"decision={res['decision']} obligations={len(res['obligations'])} gaps={len(res['gaps'])}")
    if res["risk_classification"] == "High Risk":
        ok("high-risk hiring AI classified High Risk")
    else:
        fail(f"expected High Risk, got {res['risk_classification']}")
    arts = {o["article"] for o in res["obligations"]}
    if {"Article 9", "Article 14"}.issubset(arts) and any(o["key"] == "annex3_4_employment" for o in res["obligations"]):
        ok(f"obligations mapped ({len(res['obligations'])}: incl. Art 9/14 + Annex III(4) employment)")
    else:
        fail("obligations missing expected high-risk / employment articles")
    if res["gaps"] and res["recommendations"]:
        ok(f"gap analysis + recommendations present ({len(res['gaps'])} gaps)")
    else:
        fail("no gaps/recommendations produced")
    if "not legal advice" in (res.get("disclaimer") or "").lower():
        ok("disclaimer attached to result (not legal advice)")
    else:
        fail("disclaimer missing from result")

    # Persistence
    got = requests.get(f"{API}/aiact/assessments/{aid}", headers=H(tok1)).json()["data"]
    ok("result persisted (status=scored)") if got["status"] == "scored" else fail("result not persisted")

    # Prohibited-practice case → Unacceptable
    proh = dict(HIGH_RISK); proh["assigns_social_scores"] = "yes"; proh["system_name"] = "Social scorer"
    _, s2 = create_and_score(tok1, proh)
    r2 = s2.json()["data"]["result"]
    if r2["risk_classification"] == "Unacceptable Risk" and r2["prohibited"]["has_hard_stop"]:
        ok("prohibited practice → Unacceptable Risk (hard stop)")
    else:
        fail(f"prohibited case wrong: {r2['risk_classification']}")

    # Tenant isolation
    cross = requests.get(f"{API}/aiact/assessments/{aid}", headers=H(tok2))
    listed = requests.get(f"{API}/aiact/assessments", headers=H(tok2)).json()["data"]
    if cross.status_code == 404 and listed["total"] == 0:
        ok("tenant isolation: org B cannot see org A's assessment")
    else:
        fail(f"tenant isolation breach: cross={cross.status_code} listB={listed['total']}")

    # GDPR export + delete
    exp = requests.get(f"{API}/aiact/assessments/{aid}/export", headers=H(tok1))
    if exp.status_code == 200 and exp.json()["data"]["assessment"]["id"] == aid:
        ok("GDPR export returns the full assessment")
    else:
        fail("GDPR export failed")
    dele = requests.delete(f"{API}/aiact/assessments/{aid}", headers=H(tok1))
    if dele.status_code == 200 and dele.json()["data"]["deleted"]:
        ok("GDPR delete erased the assessment")
        cleanup_ids.remove(aid)
    else:
        fail("GDPR delete failed")

    # Optional: AI narrative path (best-effort — don't fail the run if it errors)
    aid3, s3 = create_and_score(tok1, HIGH_RISK, ai=True)
    if s3 and s3.status_code == 200:
        r3 = s3.json()["data"]["result"]
        tag = "AI-generated" if r3.get("ai_generated") else "deterministic"
        print(f"    AI narrative path: ai_generated={r3.get('ai_generated')} model={r3.get('model_used')}")
        ok(f"AI narrative path returned a result ({tag})")

    return summarize()


def cleanup():
    print("\n--- cleanup ---")
    for aid in cleanup_ids:
        try:
            requests.delete(f"{SB_URL}/rest/v1/activity_log?id=eq.{aid}", headers=ADMIN_H)
        except Exception as e:
            print(f"  row {aid} cleanup failed: {e}")
    for uid in created_user_ids:
        try:
            requests.delete(f"{SB_URL}/auth/v1/admin/users/{uid}", headers=ADMIN_H)
            print(f"  deleted user {uid}")
        except Exception as e:
            print(f"  user {uid} delete failed: {e}")


def summarize():
    npass = sum(1 for s, _ in results if s == "PASS")
    nfail = sum(1 for s, _ in results if s == "FAIL")
    print(f"\n=== RESULT: {npass} passed, {nfail} failed ===")
    return nfail == 0


if __name__ == "__main__":
    good = False
    try:
        good = main()
    except Exception as e:
        import traceback; traceback.print_exc(); print(f"ERROR: {e}")
    finally:
        cleanup()
    sys.exit(0 if good else 1)
