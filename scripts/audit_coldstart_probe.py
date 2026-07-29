"""Adversarial cold-start prod probe. Creates ONE brand-new Supabase account and
drives the ACTUAL prod API as a fresh client, recording status + a snippet for
each call. Read-only intent; the only writes are synthetic test rows, cleaned up.
Does NOT place a real Aidan phone call (tests the gate with bad data instead).
"""
import base64, json, os, time, uuid, requests

B = "https://execflex-backend-1.onrender.com"
API = f"{B}/api/v1"

def le(p):
    e={}
    for l in open(p,encoding="utf-8"):
        l=l.strip()
        if l and not l.startswith("#") and "=" in l: k,v=l.split("=",1); e[k.strip()]=v.strip()
    return e
env=le(r"C:\Users\fionn\execflex-backend\.env")
SB=env["SUPABASE_URL"].rstrip("/"); KEY=env["SUPABASE_SERVICE_KEY"]
AH={"apikey":KEY,"Authorization":f"Bearer {KEY}","Content-Type":"application/json"}
PW="Test!"+uuid.uuid4().hex[:12]; TS=int(time.time())
users=[]; cleanup_cand=[]

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
def snip(r,n=180):
    try: return json.dumps(r.json())[:n]
    except: return r.text[:n]
def show(label,r):
    print(f"  [{r.status_code}] {label}: {snip(r)}")
    return r

def main():
    email=f"fionnano+cold-{TS}@gmail.com"
    uid=mkuser(email)
    tok,am=login(email)
    print(f"\n### COLD-START as brand-new user {email}")
    if not am.get("org_id"):
        print("  !! org provisioning FAILED — new user has no org_id in JWT. HARD WALL."); return
    print(f"  org_id={am['org_id']} role={am.get('role')}")

    print("\n## SUITE")
    show("GET /suite/modules", requests.get(f"{API}/suite/modules",headers=H(tok)))

    print("\n## SEARCH / CONSOLE (recruiting) — the core product")
    show("GET /jobs (fresh org — expect empty)", requests.get(f"{API}/jobs",headers=H(tok)))
    show("GET /candidates (fresh org — expect empty)", requests.get(f"{API}/candidates",headers=H(tok)))
    show("GET /pipeline", requests.get(f"{API}/pipeline",headers=H(tok)))
    show("GET /ai/status", requests.get(f"{API}/ai/status",headers=H(tok)))
    # create a candidate
    rc=show("POST /candidates (create)", requests.post(f"{API}/candidates",headers=H(tok),json={"full_name":"Test Candidate","email":f"cand-{TS}@example.com","skills":["Python"],"experience_years":5}))
    cid=None
    try: cid=rc.json()["data"]["id"]
    except: pass
    if cid: cleanup_cand.append(cid)
    # create a job
    rj=show("POST /jobs (create)", requests.post(f"{API}/jobs",headers=H(tok),json={"title":"Head of Data","required_skills":["Python"],"min_experience":3,"location":"Dublin","commitment_type":"full_time"}))
    jid=None
    try: jid=rj.json()["data"]["id"]
    except: pass
    # matching
    if jid:
        show("POST /matches (job vs pool)", requests.post(f"{API}/matches",headers=H(tok),json={"job_id":jid,"limit":10}))
    # AI: generate JD  (does the AI actually fire?)
    show("POST /ai/generate-jd (AI fire?)", requests.post(f"{API}/ai/generate-jd",headers=H(tok),json={"title":"Head of Data Engineering","company":"Acme","seniority":"Head of","industry":"FinTech"}))
    # AI: question flow
    show("GET /ai/question-flow", requests.get(f"{API}/ai/question-flow",headers=H(tok)))
    # Aidan screening GATE (bad candidate id — must NOT dial)
    show("POST /screens/phone (gate test, bogus cand)", requests.post(f"{API}/screens/phone",headers=H(tok),json={"candidate_id":str(uuid.uuid4())}))
    show("GET /screens (list)", requests.get(f"{API}/screens",headers=H(tok)))
    show("GET /compliance/decisions", requests.get(f"{API}/compliance/decisions",headers=H(tok)))
    show("GET /talent-pools", requests.get(f"{API}/talent-pools",headers=H(tok)))

    print("\n## MARKETPLACE")
    show("GET /marketplace/leaders (seed pool)", requests.get(f"{API}/marketplace/leaders",headers=H(tok)))
    show("GET /marketplace/search?q=fintech data platform", requests.get(f"{API}/marketplace/search",headers=H(tok),params={"q":"fintech data platform"}))
    show("GET /marketplace/opportunities", requests.get(f"{API}/marketplace/opportunities",headers=H(tok)))
    show("GET /marketplace/me", requests.get(f"{API}/marketplace/me",headers=H(tok)))
    show("GET /marketplace/company", requests.get(f"{API}/marketplace/company",headers=H(tok)))

    print("\n## AI ACT CHECK")
    show("GET /aiact/questions", requests.get(f"{API}/aiact/questions",headers=H(tok)))
    ra=requests.post(f"{API}/aiact/assessments",headers=H(tok),json={"system_name":"CV screen","answers":{"uses_ai":"yes","business_functions":["hr"],"affects_people":"yes","automated_hiring_decisions":"yes","in_eu":"yes"}})
    show("POST /aiact/assessments", ra)
    aid=None
    try: aid=ra.json()["data"]["id"]
    except: pass
    if aid:
        rs=show("POST /aiact/assessments/<id>/score (AI fire?)", requests.post(f"{API}/aiact/assessments/{aid}/score",headers=H(tok),json={}))
        try:
            res=rs.json()["data"]["result"]
            print(f"     -> ai_generated={res.get('ai_generated')} model={res.get('model_used')} risk={res.get('risk_classification')}")
        except: pass

def cleanup():
    print("\n--- cleanup ---")
    for cid in cleanup_cand:
        try: requests.delete(f"{SB}/rest/v1/people_profiles?id=eq.{cid}",headers=AH)
        except: pass
    for uid in users:
        try:
            requests.delete(f"{SB}/auth/v1/admin/users/{uid}",headers=AH)
            print(f"  deleted user {uid}")
        except Exception as e: print(f"  user cleanup failed: {e}")

if __name__=="__main__":
    try: main()
    except Exception as e:
        import traceback; traceback.print_exc()
    finally: cleanup()
