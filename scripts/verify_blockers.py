"""Live prod verification of the pre-launch blocker fixes (real auth'd account).

Waits for the backend deploy (polls POST /jobs until it stops 500-ing), then:
  Fix 1: POST /jobs → 201 (not 500); skills persist; GET round-trips them.
  Fix 5: POST /matches → skill-matched candidate outscores skill-blind.
  Fix 4: AI Act score → ai_generated=true (real billed call fires).
  Fix 2: marketplace browse contains no non-seed "Uni Leader".
Cleans up every synthetic artifact. Only fionnano+... aliases.
"""
import base64, json, os, sys, time, uuid, requests

B = "https://execflex-backend-1.onrender.com"; API = f"{B}/api/v1"

def le(p):
    e={}
    for l in open(p,encoding="utf-8"):
        l=l.strip()
        if l and not l.startswith("#") and "=" in l: k,v=l.split("=",1); e[k.strip()]=v.strip()
    return e
env=le(os.environ.get("BACKEND_ENV") or r"C:\Users\fionn\execflex-backend\.env")
SB=env["SUPABASE_URL"].rstrip("/"); KEY=env["SUPABASE_SERVICE_KEY"]
AH={"apikey":KEY,"Authorization":f"Bearer {KEY}","Content-Type":"application/json"}
PW="Test!"+uuid.uuid4().hex[:12]; TS=int(time.time())
users=[]; jobs=[]; cands=[]; aiacts=[]
results=[]
def ok(m): results.append(("PASS",m)); print(f"  [PASS] {m}")
def fail(m): results.append(("FAIL",m)); print(f"  [FAIL] {m}")

def mkuser(email):
    uid=requests.post(f"{SB}/auth/v1/admin/users",headers=AH,json={"email":email,"password":PW,"email_confirm":True}).json()["id"]
    users.append(uid); return uid
def login(email,tries=20):
    for _ in range(tries):
        t=requests.post(f"{SB}/auth/v1/token?grant_type=password",headers={"apikey":KEY,"Content-Type":"application/json"},json={"email":email,"password":PW}).json().get("access_token")
        if t:
            am=json.loads(base64.urlsafe_b64decode(t.split(".")[1]+"===")).get("app_metadata") or {}
            if am.get("org_id"): return t,am
        time.sleep(1.5)
    return None,{}
def H(t): return {"Authorization":f"Bearer {t}","Content-Type":"application/json"}

JOB={"title":"Head of Data Engineering","description":"Lead the data platform team end to end.",
     "commitment_type":"full-time","skills_required":["Spark","dbt","Python"],"experience_min":8,
     "location":"Dublin","industry":"FinTech","pay_range_min":120000,"pay_range_max":160000,
     "pay_range_currency":"EUR","pay_range_period":"annual","is_remote":True}

def wait_deploy(tok, tries=30):
    print("Waiting for backend deploy (POST /jobs must stop 500-ing)…")
    for i in range(tries):
        r=requests.post(f"{API}/jobs",headers=H(tok),json=JOB)
        print(f"  probe {i}: POST /jobs -> {r.status_code}")
        if r.status_code==201:
            jid=r.json()["data"]["id"]; jobs.append(jid); return jid
        if r.status_code not in (500,502,503,404):
            # 400 would be a contract issue; surface it
            print("   body:",r.text[:160])
        time.sleep(20)
    return None

def main():
    e=f"fionnano+blockers-{TS}@gmail.com"; mkuser(e)
    tok,am=login(e)
    if not am.get("org_id"): fail("org provisioning"); return summarize()
    print(f"\n### Verifying blockers as {e} (org {am['org_id'][:8]})")

    # FIX 1 — job posting
    jid=wait_deploy(tok)
    if not jid: fail("Fix 1: POST /jobs never returned 201 (deploy not live or still broken)"); return summarize()
    job=requests.get(f"{API}/jobs/{jid}",headers=H(tok)).json()["data"]
    ok(f"Fix 1: POST /jobs → 201 (type={job.get('type')}, commitment={job.get('commitment_type')})")
    if job.get("skills_required")==["Spark","dbt","Python"] and (job.get("metadata") or {}).get("required_skills"):
        ok("Fix 1: job persists skills (metadata.required_skills + round-tripped skills_required)")
    else:
        fail(f"Fix 1: skills not persisted: skills_required={job.get('skills_required')} meta={job.get('metadata')}")

    # FIX 5 — matcher scores on skills
    c1=requests.post(f"{API}/candidates",headers=H(tok),json={"full_name":"Skill Match","email":f"sm{TS}@example.com","skills":["Spark","dbt","Python"],"experience_years":11}).json().get("data",{})
    c2=requests.post(f"{API}/candidates",headers=H(tok),json={"full_name":"Skill Blind","email":f"sb{TS}@example.com","skills":["Cobol","Fortran"],"experience_years":11}).json().get("data",{})
    for c in (c1,c2):
        if c.get("id"): cands.append(c["id"])
    m=requests.post(f"{API}/matches",headers=H(tok),json={"job_id":jid,"limit":10})
    if m.status_code==200:
        by={x["candidate_name"]:x["score"] for x in m.json()["data"]["matches"]}
        sm,sb=by.get("Skill Match"),by.get("Skill Blind")
        print(f"     match scores: Skill Match={sm} Skill Blind={sb}")
        if sm is not None and sb is not None and sm>sb:
            ok(f"Fix 5: skill-matched outscores skill-blind ({sm} > {sb}) on a real posted job")
        else:
            fail(f"Fix 5: skill matching not working (sm={sm} sb={sb})")
    else:
        fail(f"Fix 5: /matches HTTP {m.status_code}: {m.text[:150]}")

    # FIX 4 — AI Act AI narrative fires
    ca=requests.post(f"{API}/aiact/assessments",headers=H(tok),json={"system_name":"CV screen","answers":{"uses_ai":"yes","business_functions":["hr"],"affects_people":"yes","automated_hiring_decisions":"yes","in_eu":"yes","human_oversight":"no"}})
    aid=ca.json()["data"]["id"]; aiacts.append(aid)
    sc=requests.post(f"{API}/aiact/assessments/{aid}/score",headers=H(tok),json={})
    res=sc.json().get("data",{}).get("result",{})
    print(f"     aiact: ai_generated={res.get('ai_generated')} model={res.get('model_used')} risk={res.get('risk_classification')}")
    if res.get("ai_generated") is True:
        ok(f"Fix 4: AI Act narrative fires a real billed call (ai_generated=true, {res.get('model_used')})")
    else:
        fail(f"Fix 4: AI Act still deterministic (ai_generated={res.get('ai_generated')}) — key missing in prod?")

    # FIX 2 — no fake leader in live pool
    pool=requests.get(f"{API}/marketplace/leaders",headers=H(tok)).json()["data"]["leaders"]
    names=[l["name"] for l in pool]
    if "Uni Leader" not in names and all("Verify" not in n and "Uni " not in n for n in names):
        ok(f"Fix 2: live marketplace pool has no fake leaders ({len(pool)} leaders, seed only)")
    else:
        fail(f"Fix 2: fake leader still visible: {[n for n in names if 'Uni' in n or 'Verify' in n]}")

    return summarize()

def cleanup():
    print("\n--- cleanup ---")
    for jid in jobs: requests.delete(f"{SB}/rest/v1/opportunities?id=eq.{jid}",headers=AH)
    for cid in cands: requests.delete(f"{SB}/rest/v1/people_profiles?id=eq.{cid}",headers=AH)
    for aid in aiacts: requests.delete(f"{SB}/rest/v1/aiact_assessments?id=eq.{aid}",headers=AH)
    for uid in users:
        requests.delete(f"{SB}/auth/v1/admin/users/{uid}",headers=AH); print(f"  deleted user {uid}")

def summarize():
    p=sum(1 for s,_ in results if s=="PASS"); f=sum(1 for s,_ in results if s=="FAIL")
    print(f"\n=== RESULT: {p} passed, {f} failed ===")
    return f==0

if __name__=="__main__":
    good=False
    try: good=main()
    except Exception as e:
        import traceback; traceback.print_exc()
    finally: cleanup()
    sys.exit(0 if good else 1)
