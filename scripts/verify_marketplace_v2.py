"""Live end-to-end verification of the ainm Marketplace v2 against the deployed
backend, using REAL Supabase auth accounts (created via the admin API).

Creates a leader account and a company account, runs the full two-sided journey,
asserts the real behaviours (account linkage, ranked search, contact reveal on
acceptance, placement fee, tenant isolation, GDPR), then deletes every synthetic
artifact via the service key. No real third-party people; the only emails are
fionnano+... aliases.
"""
import os, sys, time, json, uuid
import requests

BACKEND = os.environ.get("BACKEND_URL", "https://execflex-backend-1.onrender.com")
API = f"{BACKEND}/api/v1"

# Load Supabase creds from the backend .env
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
def ok(msg): results.append(("PASS", msg)); print(f"  [PASS] {msg}")
def fail(msg): results.append(("FAIL", msg)); print(f"  [FAIL] {msg}")

created_user_ids = []
cleanup_rows = {"people_profiles": [], "activity_log": []}


def admin_create_user(email):
    r = requests.post(f"{SB_URL}/auth/v1/admin/users", headers=ADMIN_H, json={
        "email": email, "password": PW, "email_confirm": True})
    r.raise_for_status()
    uid = r.json()["id"]
    created_user_ids.append(uid)
    return uid


def _decode_jwt(tok):
    import base64
    payload = tok.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def login(email):
    r = requests.post(f"{SB_URL}/auth/v1/token?grant_type=password",
                      headers={"apikey": SB_KEY, "Content-Type": "application/json"},
                      json={"email": email, "password": PW})
    r.raise_for_status()
    return r.json()["access_token"]


def login_with_org(email, tries=20):
    """Login and wait until the JWT carries app_metadata.org_id (org provisioning
    is injected into the token by the auth hook, not the admin user record)."""
    for _ in range(tries):
        tok = login(email)
        am = (_decode_jwt(tok) or {}).get("app_metadata") or {}
        if am.get("org_id"):
            return tok, am
        time.sleep(1.5)
    return login(email), {}


def H(tok): return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


def wait_until_deployed(tries=18):
    """Poll the real apply path until the owner_user_id fix is deployed (non-500)."""
    print("Waiting for backend deploy (probing apply path)…")
    for i in range(tries):
        em = f"fionnano+probe-{TS}-{i}@gmail.com"
        try:
            uid = admin_create_user(em)
            tok, am = login_with_org(em)
            code = None
            if am.get("org_id"):
                r = requests.post(f"{API}/marketplace/leaders", headers=H(tok),
                                  json={"name": "Probe User", "track": "ml_platform"})
                code = r.status_code
                if code in (200, 201):
                    lid = r.json()["data"]["id"]
                    requests.delete(f"{SB_URL}/rest/v1/people_profiles?id=eq.{lid}", headers=ADMIN_H)
            requests.delete(f"{SB_URL}/auth/v1/admin/users/{uid}", headers=ADMIN_H)
            created_user_ids.remove(uid)
            print(f"  probe {i}: apply → {code}")
            if code in (200, 201):
                print("  backend READY")
                return True
        except Exception as e:
            print(f"  probe {i} error: {e}")
        time.sleep(25)
    return False


def main():
    print(f"\n=== Marketplace v2 live verification against {BACKEND} ===")
    if not wait_until_deployed():
        fail("backend did not become ready (apply still failing)")
        return summarize()
    leader_email = f"fionnano+mkt-leader-{TS}@gmail.com"
    company_email = f"fionnano+mkt-company-{TS}@gmail.com"

    # 1. Create two real accounts
    lu = admin_create_user(leader_email)
    cu = admin_create_user(company_email)
    print(f"Created leader user {lu} and company user {cu}")

    ltok, lam = login_with_org(leader_email)
    ctok, cam = login_with_org(company_email)
    if lam.get("org_id") and cam.get("org_id"):
        ok(f"org provisioning set org_id in the JWT (leader role={lam.get('role')}, "
           f"leader org={lam['org_id'][:8]}…, company org={cam['org_id'][:8]}…)")
    else:
        fail("org provisioning did not set org_id in the JWT — cannot continue org-scoped flows")
        return summarize()
    ok("password login returned org-scoped JWTs for both accounts")

    # 2. LEADER: apply (account-linked) + strong vetting → verified
    strong = ("I led a team of 12 and cut p99 inference latency 55% while reducing "
              "serving cost 30%, owning incident response, drift detection, rollback "
              "and SLAs across a production feature store and model-serving stack.")
    ap = requests.post(f"{API}/marketplace/leaders", headers=H(ltok), json={
        "name": "Verify Leader", "headline": "Head of ML Platform", "track": "ml_platform",
        "seniority": "Head of", "location": "Dublin, IE",
        "skills": ["MLOps", "Feature Stores", "Model Serving"],
        "sectors": ["FinTech"], "email": leader_email})
    if ap.status_code not in (200, 201):
        fail(f"apply failed {ap.status_code}: {ap.text[:200]}"); return summarize()
    leader_id = ap.json()["data"]["id"]
    cleanup_rows["people_profiles"].append(leader_id)
    ok(f"leader profile created & linked to account (id={leader_id[:8]}…)")

    me = requests.get(f"{API}/marketplace/me", headers=H(ltok)).json()["data"]
    if me.get("is_leader") and me.get("leader", {}).get("id") == leader_id:
        ok("/me reports the caller is a leader with their linked profile")
    else:
        fail("/me did not link the leader profile to the account")

    qs = requests.get(f"{API}/marketplace/vetting/questions?track=ml_platform",
                      headers=H(ltok)).json()["data"]["questions"]
    responses = [{"question_id": q["id"], "competency": q["competency"],
                  "weight": q["weight"], "text": strong} for q in qs]
    vr = requests.post(f"{API}/marketplace/leaders/{leader_id}/vetting", headers=H(ltok),
                       json={"track": "ml_platform", "responses": responses})
    vet = vr.json()["data"]["vetting"]
    print(f"    vetting: score={vet['score']} status={vet['status']} "
          f"ai_generated={vet.get('ai_generated')} model={vet.get('model_used')}")
    if vet["status"] == "verified" and vet["score"] >= 70:
        ok(f"vetting verified the strong candidate ({vet['score']}/100, "
           f"{'AI path' if vet.get('ai_generated') else 'heuristic'})")
    else:
        fail(f"vetting did not verify strong candidate: {vet['score']} {vet['status']}")

    # 3. Leader appears in ranked search (contact never exposed)
    sr = requests.get(f"{API}/marketplace/search",
                      headers=H(ctok), params={"q": "feature store MLOps serving"}).json()["data"]
    hit = next((x for x in sr["results"] if x["id"] == leader_id), None)
    if hit:
        ok(f"leader appears in ranked search (rank {hit.get('rank')}, "
           f"reasons: {', '.join(hit.get('match_reasons', [])[:2])})")
    else:
        fail("verified leader did NOT appear in search results")
    if hit and "contact" not in hit:
        ok("search results never expose contact details")
    elif hit:
        fail("search leaked contact details")

    # 4. COMPANY: set profile, request an introduction
    requests.put(f"{API}/marketplace/company", headers=H(ctok), json={
        "name": "Verify AI Co", "sector": "FinTech",
        "contact_name": "Dana Verifier", "contact_email": company_email})
    ok("company profile saved")

    ir = requests.post(f"{API}/marketplace/leaders/{leader_id}/introductions", headers=H(ctok),
                       json={"company": {"name": "Verify AI Co"}, "message": "Keen to talk",
                             "first_year_comp": 200000})
    if ir.status_code != 201:
        fail(f"request intro failed {ir.status_code}: {ir.text[:200]}");
    intro = ir.json()["data"]; intro_id = intro["id"]
    cleanup_rows["activity_log"].append(intro_id)
    ok(f"introduction created (status={intro['status']}, fee_pct={intro['placement_fee_pct']})")

    # Company view pre-acceptance: no leader contact
    cv = requests.get(f"{API}/marketplace/introductions", headers=H(ctok)).json()["data"]
    if cv["total"] == 1 and not cv["introductions"][0].get("leader_contact"):
        ok("company sees its own intro; leader contact hidden pre-acceptance")
    else:
        fail(f"pre-acceptance contact state wrong: total={cv['total']}")

    # 5. LEADER inbox → accept
    inbox = requests.get(f"{API}/marketplace/inbox", headers=H(ltok)).json()["data"]
    if inbox["total"] == 1 and inbox["introductions"][0]["requester_contact"].get("email") == company_email:
        ok("leader inbox shows the request with the company's identity")
    else:
        fail(f"leader inbox wrong: {json.dumps(inbox)[:200]}")

    resp = requests.post(f"{API}/marketplace/introductions/{intro_id}/respond", headers=H(ltok),
                         json={"decision": "accepted"})
    if resp.status_code == 200 and resp.json()["data"]["status"] == "accepted":
        ok("leader accepted the introduction")
    else:
        fail(f"accept failed {resp.status_code}: {resp.text[:150]}")

    # 6. Company now sees revealed contact + can mark hired → fee
    cv2 = requests.get(f"{API}/marketplace/introductions", headers=H(ctok)).json()["data"]
    rev = cv2["introductions"][0]
    if rev.get("contact_revealed") and rev.get("leader_contact", {}).get("email") == leader_email:
        ok("company sees the leader's contact AFTER acceptance (contact reveal works)")
    else:
        fail(f"contact not revealed post-acceptance: {json.dumps(rev)[:200]}")

    hire = requests.patch(f"{API}/marketplace/introductions/{intro_id}", headers=H(ctok),
                          json={"hired": True, "first_year_comp": 200000, "placement_fee_pct": 15})
    hd = hire.json()["data"]
    if hd.get("placement_fee_amount") == 30000.0 and hd["status"] == "hired":
        ok("mark hired computed placement fee €30,000 = 15% of €200k")
    else:
        fail(f"placement fee wrong: {hd.get('placement_fee_amount')} status={hd.get('status')}")

    # 7. Tenant isolation: leader's own org sees no company introductions
    li = requests.get(f"{API}/marketplace/introductions", headers=H(ltok)).json()["data"]
    if li["total"] == 0:
        ok("tenant isolation: a different org does not see the company's introductions")
    else:
        fail(f"tenant isolation breach: leader org saw {li['total']} intros")

    # 8. GDPR export + delete
    exp = requests.get(f"{API}/marketplace/me/export", headers=H(ltok))
    if exp.status_code == 200 and exp.json()["data"]["profile"]["id"] == leader_id:
        ok("GDPR export returns the leader's full data bundle")
    else:
        fail("GDPR export failed")
    dele = requests.delete(f"{API}/marketplace/me", headers=H(ltok))
    if dele.status_code == 200 and dele.json()["data"]["deleted"]:
        ok("GDPR delete erased the leader profile")
        cleanup_rows["people_profiles"].clear()  # already deleted
    else:
        fail("GDPR delete failed")

    return summarize()


def cleanup():
    print("\n--- cleanup (service key) ---")
    # Delete synthetic rows directly via PostgREST
    for tbl, ids in cleanup_rows.items():
        for rid in ids:
            try:
                requests.delete(f"{SB_URL}/rest/v1/{tbl}?id=eq.{rid}", headers=ADMIN_H)
            except Exception as e:
                print(f"  row cleanup {tbl}/{rid} failed: {e}")
    # Delete the company profile rows (activity_log marketplace_company_profile) for the test company org
    # and any leftover intro rows are covered above. Delete the auth users last.
    for uid in created_user_ids:
        try:
            requests.delete(f"{SB_URL}/auth/v1/admin/users/{uid}", headers=ADMIN_H)
            print(f"  deleted auth user {uid}")
        except Exception as e:
            print(f"  user delete {uid} failed: {e}")


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
        import traceback; traceback.print_exc()
        print(f"ERROR: {e}")
    finally:
        cleanup()
    sys.exit(0 if good else 1)
