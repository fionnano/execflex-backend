# LAUNCH READINESS AUDIT — adversarial pre-launch (2026-07-29)

**Method:** probed PRODUCTION, not local. Created real Supabase accounts and drove
the actual prod API cold-start. Queried the prod DB directly via the service key.
Assumed every prior "verified" claim was suspect and re-proved it. **Audit only —
nothing was fixed.** The one thing I changed was deleting a couple of test rows my
OWN probes created this session (so I didn't add cruft); I did NOT delete the
pre-existing leak (reported below as evidence).

**Headline:** **NOT launch-ready for the flagship "Search" recruiting product.**
A brand-new client **cannot post a job** — the core action returns HTTP 500
(reproduced). Everything downstream (matching, screening a candidate against a
job) is blocked by it. There is also a **fake test leader live in the marketplace
pool** right now. Marketplace and AI Act Check are in better shape. Details below.

---

## 1. COLD-START CLIENT JOURNEY (the #1 section)

Traced as a brand-new Supabase user (`fionnano+cold-…@gmail.com`) against prod.
Signup/org provisioning **works** — a fresh user gets `org_id`/`role=owner` in the
JWT and lands on `/suite`.

### ainm Search / `/console` (recruiting) — ⛔ BROKEN core flow
| Step | Result |
|---|---|
| Land on `/suite`, open Search | ✅ 200, clean empty dashboard |
| `GET /jobs`, `/candidates`, `/pipeline` | ✅ 200, empty (correct for a new org) |
| `POST /candidates` (add a candidate) | ✅ 201 works |
| **`POST /jobs` (post a job)** | ⛔ **HTTP 500** — `null value in column "type"` (Postgres 23502). Reproduced directly. |
| `POST /matches` (match candidates to a job) | ⛔ blocked — needs a job, which can't be created |
| `POST /screens/phone` (Aidan calls candidate) | ⚠️ gate works (400 "phone is required"); **happy-path (real call) UNTESTED** — can't safely place a live call in an audit |
| `POST /ai/generate-jd` | ✅ fires real AI (see §3) but does NOT create a job |

**This is a dead end.** A recruiter signs up, adds a candidate, tries to post their
first job → **500**. No job → no matching, no interview kit, no screening against a
role. The single most important action of the flagship product fails on day one.
(29 jobs exist in prod, but they're legacy/seed rows created by an older path with
`type` set — not proof the current endpoint works. It does not.)

Root cause + cascade (see §4): `POST /jobs` omits the NOT-NULL `type` enum column;
the frontend job form additionally sends `commitment_type: "full-time"` (hyphen)
where the DB enum is `full_time` (underscore); and `skills_required`/`experience`
from the form are never persisted where matching reads them. The v1 console
job→match flow was almost certainly never exercised end-to-end in prod.

### Marketplace `/marketplace` — ✅ mostly works, ⚠️ one data leak
| Step | Result |
|---|---|
| Browse vetted pool | ✅ 200, 13 verified leaders **+ 1 fake "Uni Leader"** (see §4) |
| Search (`/search?q=…`) | ✅ 200, ranked with reasons (deterministic — AI re-rank flag off, see §3) |
| Opportunities / companies | ✅ 200 |
| `GET /me`, `/company` | ✅ 200 (null company for a fresh org — correct) |
| Get vetted (apply → vetting) | ✅ works; vetting fires real AI (verified prior runs) |
| Request introduction | ✅ works |

A company user can genuinely browse, search, and request an introduction. **But the
first thing they see includes a fake leader** ("Uni Leader", empty bio).

### AI Act Check `/ai-act` — ✅ works, ⚠️ deterministic-only in prod
| Step | Result |
|---|---|
| Questions + disclaimer | ✅ 200 |
| Create → answer → score | ✅ 200, returns risk tier + obligations + gaps |
| AI narrative | ⚠️ `ai_generated=false model=aiact_deterministic_v1` — the AI narrative is OFF in prod (`AIACT_AI` unset). Rule-based only. The UI is honest about this; the **marketing claim** is the risk (§5). |

### `/suite` shell — ✅ renders, ⚠️ "one login" is partial
- `GET /suite/modules` → 200, 5 modules. Internal (Search/Marketplace/AI Act) are
  genuinely one login. **HR (ainm.ai) and Transparency open a SEPARATE app with a
  SEPARATE login** — a client clicking them from the suite home gets bounced to a
  second sign-in. Labeled "Separate sign-in", but still a launch-day "why am I
  logged out?" moment.

### ainm.ai / transparency.ainm.ai / comply.ainm.ai — probed at surface only
All three serve real apps (HTTP 200; `ainm.ai/health` 200). **I did NOT cold-start
ainm.ai** — it is the LIVE client product (Republic of Work) and creating test
accounts risks polluting the client's instance. **Its new-user flow is UNTESTED in
this audit and must be tested by the owner before launch.**

---

## 2. DEPLOYED vs. CLAIMED

| Repo | Deployed (prod) | Claimed | Gap |
|---|---|---|---|
| execflex-backend | main `2b3b4ef` on Render | SHIPPED_* all "verified" | **`POST /jobs` 500 was never caught** — prior "verifications" tested marketplace/aiact/suite, never the console job→match flow end-to-end. |
| execo-bridge | main `92b1d5b` (bundle `index-CwjJDDjk.js`) on Hetzner | unified shell + all modules | JobForm ships a broken contract (enum + missing type); never exercised against prod. |
| agentic-core | v0.18.0 (git-pinned) | compliance + recruitment agents | OK — consumed at the pin; JD/vetting fire. |
| hr-advisory-agent (ainm.ai) | live (own host/auth) | LIVE client product | **Local working tree is on branch `mobile-ux-signin` with 78 uncommitted files** and 2 unmerged branches — the owner's local state for the LIVE product is messy. Deployed prod is separate and up, but this is a footgun. |
| transparency-platform | `master`, live | pay-transparency packs | On `master` (not main); 2 untracked design dirs; unmerged branches appear empty. Product surface UNTESTED here. |
| governance-platform (comply.ainm.ai) | live | EU AI Act compliance | A SECOND AI Act product overlapping execflex `/ai-act` (§5 positioning risk). |

Stale branches everywhere (execflex-backend has ~8 local branches; `ai-debug-errors`,
`audit-2026-07` are old and mostly deletions — not un-deployed fixes). No committed
secrets in any repo (`.env` untracked). `security-hardening` is fully merged into
main (0 commits ahead).

---

## 3. AI FEATURES — FIRES vs. DOESN'T (probed in prod)

| Feature | Status | Evidence |
|---|---|---|
| **JD generation** (`/ai/generate-jd`) | 🔥 **FIRES** (real billed call) | HTTP 201, `ai_generated:true`, `cost_usd:0.012`, real posting text. **But ~19s latency** and returns **500 on LLM failure** (no silent stub — honest). |
| **Marketplace vetting** (Haiku+Sonnet) | 🔥 FIRES | verified repeatedly (`ai_generated:true`, 74/100). Falls back to a heuristic marked `ai_generated:false` if the LLM fails — honest. |
| **AI Act scoring** | ⚠️ **DETERMINISTIC-ONLY in prod** | `ai_generated:false model=aiact_deterministic_v1`. The AI narrative is behind `AIACT_AI` (unset in prod). Classification/obligations/gaps are rule-based by design (honest); the "AI" part is off. |
| **Marketplace search AI re-rank** | ⚠️ OFF by default | behind `MARKETPLACE_SEARCH_AI` / `?ai=1`; lexical ranking only in prod unless toggled. |
| **Match re-rank** (`/matches`) | 🔥 wired + flag on (`match_rerank:true`) | fires when candidates exist; **but blocked in practice** because no job can be created. Also un-rate-limited billed AI. |
| **Aidan voice screening** (Twilio/OpenAI Realtime) | ❓ **UNTESTED** | can't place a real call in an audit. Gate works. The flagship "AI recruiter that calls candidates" is **unproven in prod today**. |
| **Cara voice reception** | ❓ **UNTESTED** | same — voice path not exercisable safely here. |
| **Pay-transparency packs** (transparency.ainm.ai) | ❓ UNTESTED | separate product, not exercised. |

No dishonest silent stubs found in what I could test (JD 500s rather than faking;
AI Act/vetting mark deterministic results honestly). The **`AI_DEBUG_ERRORS`** flag
exists specifically because AI routes otherwise swallow errors — worth turning on
in prod to catch silent degradations.

---

## 4. BREAKS UNDER A REAL USER

1. ⛔ **`POST /jobs` → 500 (NOT NULL `type`).** `routes/api_v1/jobs.py:57` builds the
   insert row without the `type` column, which is a NOT-NULL enum on `opportunities`
   (`hire_fractional`/`hire_ned`). **Reproduced** directly against prod:
   `23502 null value in column "type"`. Every console job post fails.
2. ⛔ **`commitment_type` enum mismatch.** Frontend `JobForm` zod enum is
   `["full-time","part-time","contract","interim"]` (hyphens); DB enum is
   `fractional/full_time/part_time/contract` (underscores). Even with `type` fixed,
   `"full-time"` would violate the enum. `"interim"` isn't a DB value at all.
3. ⛔ **Job skills/experience dropped.** `CreateJobInput.skills_required`/
   `experience_min` are not persisted by `POST /jobs` (it only stores `metadata`),
   yet `matches.py` reads `metadata.required_skills`/`metadata.min_experience`. So
   even a fixed job would match everyone at ~50% — **matching is effectively blind.**
4. 🐛 **Live marketplace test-data leak.** `marketplace_leaders` contains **"Uni
   Leader" (vetting_status=verified, empty bio, user_id=null)** — a leftover from a
   prior verify script whose cleanup deleted the leader BEFORE its introduction
   (leader_id FK is RESTRICT → the leader delete silently failed). A client sees a
   fake leader in the live pool today.
5. 💸 **Un-rate-limited billed AI.** `/ai/generate-jd` and `/matches` (rerank) place
   real Anthropic calls with **no rate limit** — a logged-in user can spam them and
   run up cost. (Vetting, AI Act, and screens ARE rate-limited.)
6. 🔐 **Tenant isolation: OK.** Re-verified — marketplace, AI Act, and console reads
   are all org-scoped; a second org sees nothing of the first. No holes found.
7. 🗃️ **Migrations / repoint: DONE but dual-stored.** Marketplace + AI Act are live
   on the dedicated tables (verified). **The old namespaced rows were left in place**
   (intentional, for reversibility) — so the marketplace pool is dual-stored; the
   leftover `people_profiles@MARKETPLACE_ORG` etc. is cruft to clean post-launch.
8. 📱 **Mobile:** built mobile-first (`sm:`/`lg:` breakpoints, mobile drawer, safe
   switcher panel) and pages return 200, but **not pixel-verified at 375/1440** in
   this audit — needs a human visual pass.
9. 🔡 **Encoding:** `€175k–205k` serialized correctly (`€…–`) — renders
   fine, no mojibake seen. Low risk. (Cosmetic: `datetime.utcnow()` deprecation
   warnings in `email_sender.py`.)
10. 🧪 **Input validation:** marketplace + AI Act validate against allowed options;
    console `/candidates` accepts fairly loose input (didn't crash on my data, but
    validation is lighter than the newer surfaces).
11. 🔑 **Keys/tokens:** no committed secrets. I can only see LOCAL `.env`, not Render/
    Hetzner env — **prod key hygiene (rotation, stale keys) could not be audited** and
    should be checked by the owner.

---

## 5. THE SELLABLE-CLAIMS LIST (launch-safety)

**✅ TRUE-AND-PROVABLE-NOW**
- "Independently vetted AI/data leaders, scored by a real AI assessment (Haiku+Sonnet) with an explainable rationale" — marketplace vetting fires, verified.
- "EU AI Act readiness check: risk tier, the obligations you're subject to, your gaps, a readiness score" — works; rule-based and explainable.
- "AI-generated, pay-transparency-compliant job descriptions with gender-neutral language checks" — `/ai/generate-jd` fires for real.
- "One login across Search, Marketplace, and AI Act Check" — true for those three.
- "Multi-tenant, org-isolated; GDPR export/delete" — verified.

**🟡 TRUE-BUT-UNTESTED (don't demo live without a dry run)**
- "Aidan, our AI recruiter, calls and screens your candidates" — code is deployed and the gate works, but the **live call happy-path is unproven in this audit.** Test a real call before claiming it on stage.
- "Cara voice reception" — same.
- "Pay-transparency reporting packs" (transparency.ainm.ai) — not exercised.
- "Mobile-ready" — built responsive, not pixel-verified.

**🔴 NOT-YET-TRUE**
- "Post a job and instantly match your best candidates" — **job posting 500s; matching is blind** even if fixed. Do NOT demo the console job→match flow.
- "One seamless login across the whole ainm suite (incl. HR & Transparency)" — HR and Transparency are **separate logins** (shell-linked only).
- "AI-powered EU AI Act assessment" — the assessment is **rule-based in prod**; the AI narrative is off (`AIACT_AI` unset).

**⚠️ WOULD-EMBARRASS-IF-CLAIMED**
- Any live console demo of posting a job (500 on stage).
- "Browse our curated marketplace" while **"Uni Leader"** is visibly fake in the pool.
- "AI writes your EU AI Act assessment" (it's deterministic in prod).
- "Two AI Act products" positioning — **execflex `/ai-act` AND comply.ainm.ai both claim EU AI Act compliance.** Pick one story or press will notice the overlap.

---

## 6. RANKED FIX LIST (before a client logs in)

| # | Fix | Client impact | Effort |
|---|---|---|---|
| 1 | **`POST /jobs`: add `type` to the insert** (map commitment→`hire_fractional`/`hire_ned` or default) so job posting stops 500-ing | ⛔ blocks the flagship product | ~15 min |
| 2 | **Fix `commitment_type` enum** (frontend `full-time`→`full_time`; drop `interim`) | ⛔ job post fails | ~15 min |
| 3 | **Persist job skills/experience** into `metadata.required_skills`/`min_experience` (or fix matches.py to read where they're stored) so matching isn't blind | ⛔ matching returns garbage | ~30 min |
| 4 | **Delete "Uni Leader"** (and any other non-seed test rows) from `marketplace_leaders`; add cleanup-ordering fix to verify scripts | 🐛 fake data in the live pool | ~10 min |
| 5 | **Test the console job→match→screen flow end-to-end** in prod with a real job (after 1–3) — this flow has never been proven | ⛔ confidence | ~1 hr |
| 6 | **Test Aidan (real call) + Cara** end-to-end in prod | ❓ flagship AI claim unproven | ~1 hr |
| 7 | **Decide AI Act positioning** (execflex /ai-act vs comply.ainm.ai) and either enable `AIACT_AI` in prod or stop calling it "AI-powered" | ⚠️ press embarrassment | ~30 min (decision) |
| 8 | **Rate-limit `/ai/generate-jd` and `/matches`** (billed AI) | 💸 cost abuse | ~30 min |
| 9 | **Own the ainm.ai new-user flow test** (I didn't, to protect the live client) | ❓ live product | ~30 min |
| 10 | **Add pay-range/enum validation to the JobForm UI** so a client sees a friendly message, not a 500 | UX | ~30 min |
| 11 | Mobile visual pass at 375/1440; audit prod env key hygiene; clean up stale branches + hr-advisory-agent's 78 uncommitted files | polish/hygiene | ~2 hrs |

---

## VERDICT

**Not launch-ready as-is — but close, and the gap is narrow and mechanical.** The
Marketplace and AI Act Check products genuinely work cold-start for a new client,
auth/tenant-isolation/GDPR are solid, and the headline AI (vetting, JD generation)
fires for real. **The blocker is the flagship "Search" recruiting console: a new
client cannot post a job (confirmed 500), which dead-ends the entire recruit→match→
screen story, and there is a fake test leader live in the marketplace right now.**
The minimum to be launch-safe: fix the three job-posting contract bugs (#1–3, ~1
hour of code), delete the leaked test leader (#4), then **prove the console
job→match→screen flow and a real Aidan call end-to-end in prod** (#5–6) — because
nothing currently demonstrates that the recruiting flagship works with real input.
Until #5–6 pass, do not demo the console live and do not claim "post a job and
match instantly" or "AI recruiter calls your candidates" to press. Marketplace and
AI Act Check can be shown today, provided the fake leader is removed and the AI Act
"AI-powered" claim is softened to "practitioner-built, EU AI Act aware."
