# BLOCKERS_FIXED — pre-launch fix run (2026-07-29)

Fixes for the launch blockers in `LAUNCH_READINESS_AUDIT.md`. Every code fix is
committed, pushed, deployed, and **verified against PRODUCTION with a real
authenticated account**. Repos touched: **execflex-backend**, **execo-bridge**,
**agentic-core** (version only) — nothing else (see "Deferred / not touched").

**Live prod verification: 5/5 passed** (`scripts/verify_blockers.py`, real
Supabase account, all artifacts cleaned up):

```
[PASS] Fix 1: POST /jobs → 201 (type=hire_fractional, commitment=full_time)
[PASS] Fix 1: job persists skills (metadata.required_skills + round-tripped skills_required)
[PASS] Fix 5: skill-matched outscores skill-blind (51.5 > 26.5) on a real posted job
[PASS] Fix 4: AI Act narrative fires a real billed call (ai_generated=true, sonnet)
[PASS] Fix 2: live marketplace pool has no fake leaders (13 verified, seed only)
```

Deployed: execflex-backend main `d96d039` (Render, agentic-core reinstalled to
v0.18.1); execo-bridge main (Hetzner bundle `index-DSw3V1vm.js`, HTTP 200);
agentic-core `v0.18.1` tagged/pushed.

---

## FIX 1 — Job posting 500 ✅ (execflex-backend + execo-bridge)

**Was:** `POST /api/v1/jobs` 500'd — `null value in column "type"` (the handler
omitted the NOT-NULL `opportunities.type` enum). Compounded by a `commitment_type`
enum mismatch (frontend `full-time` vs DB `full_time`) and job skills/experience
never persisted where the matcher reads them.

**Fix:** `routes/api_v1/jobs.py` now populates `type` (default `hire_fractional`,
→ `hire_ned` when `is_ned`/`role_type`), normalises `commitment_type` to the DB
enum (`full-time`→`full_time`, `interim`→`contract`, …), and routes
`skills_required`/`experience_min` into `metadata.required_skills`/`min_experience`
(and serialises them back on GET so the edit form round-trips). Frontend `JobForm`
enum reconciled to DB values (`full_time`/`part_time`/`contract`/`fractional`).
Backend normalisation is a permanent safety net for any client.

**Prod verification:** a brand-new account posted a job → **201** (`type=hire_fractional`,
`commitment_type=full_time`); `GET /jobs/{id}` returned `skills_required:
["Spark","dbt","Python"]` and `metadata.required_skills` populated.

## FIX 5 — Skill matching ✅ (execflex-backend)

**Fix:** with Fix 1(c), a posted job's skills reach the Role the matcher builds.
Added `test/test_jobs_matching.py` proving the seam (skill-matched > skill-blind;
skills_fit 100 vs 0) at the exact point that was broken.

**Prod verification:** for the real posted job, `POST /matches` scored the
skill-matched candidate **51.5** vs the skill-blind **26.5** — skill-based matching
is genuinely true now.

## FIX 2 — Fake leader ✅ (execflex-backend, data)

**Was:** a fake **"Uni Leader"** (verified, empty bio) sat in the live
`marketplace_leaders` pool — a leftover from a verify script whose cleanup hit the
intro→leader FK ordering.

**Fix:** deleted it via the service key, removing any addressed introductions and
vetting-assessment rows first (FK-safe). **Prod verification:** the live browse
endpoint returns **13 verified leaders, seed-only** — no "Uni Leader", no non-seed
names.

## FIX 4 — AI Act "AI-powered" ✅ (execflex-backend) — chose option (a)

**Was:** `/ai-act` scoring was deterministic in prod (`ai_generated=false`) while
the UI implies AI. **Choice logged: option (a) — enable the AI narrative.** Render
env vars aren't settable from here, so the deployable lever is the code default:
`services/aiact/engine.py::_ai_enabled()` now returns true when
`ANTHROPIC_API_KEY` is present (disable with `AIACT_AI=off`), mirroring the
marketplace vetting engine. The key IS present in prod. The deterministic path
still stands in on any AI failure, and the UI already shows the "✨ AI-generated"
marker only when `ai_generated=true`, so it is now truthful.

**Prod verification:** a real AI Act score returned **`ai_generated=true`,
`model=aiact_ai_v1 (agentic-core scoring_engine / sonnet)`** — a real billed call
fires. (Tests forced to `AIACT_AI=off` to stay deterministic offline.)

## FIX 6 — "One login" honesty ✅ (execo-bridge) — verified, no change needed

The `/suite` copy already scopes the single-login claim correctly: *"Search,
Marketplace and AI Act Check share this single login,"* and HR/Transparency carry a
**"Separate sign-in"** badge (both the SuiteHome cards and the SuiteSwitcher).
Swept the whole frontend for overstated SSO copy — none found. No change made.

## FIX 3 — Forgot-password ✅ (execflex-backend + execo-bridge) — no lie in scope

execflex + marketplace share **one passwordless magic-link** auth surface
(`AuthForm` → `supabase.auth.signInWithOtp`). It is honest: real send, "Check your
email" shown only after success, error-handled, and "Resend link" genuinely
re-sends. **There is no forgot-password stub/lie here.** `AuthContext.resetPassword`
is wired to real Supabase but is dead code (no UI renders it) — left as-is (removing
it would be scope creep; it makes no false claim). **Prod check:** the OTP endpoint
returns 200 (Supabase accepts sends). ⚠️ **Actual email deliverability depends on
Supabase SMTP config — see "Verify by hand" below.**

## FIX 7 — agentic-core version ✅ (agentic-core)

`__version__` was stale at `0.17.0` while the package was tagged `v0.18.0`. Bumped
to **0.18.1** (new patch tag — avoids force-moving `v0.18.0`), and rewrote
`tests/test_smoke.py::test_version` to **derive the expected version from
pyproject** so it can never drift again. Tagged/pushed `v0.18.1`; bumped execflex's
pin to `@v0.18.1`. **Prod verification:** the AI Act call firing through
agentic-core's `scoring_engine` (Fix 4) confirms v0.18.1 reinstalled and is live.

---

## ⚠️ VERIFY BY HAND (not fully provable in this run — owner must check)

1. **Magic-link email deliverability.** Login is passwordless magic-link ONLY. The
   OTP endpoint returns 200, but I cannot confirm the email actually lands in a real
   inbox at launch volume. **Confirm Supabase has a production SMTP provider
   configured** (the default Supabase mailer is rate-limited to a few/hour and may
   only send to team addresses). If SMTP isn't set, no real client can log in.
2. **Aidan (voice screening) + Cara (voice) happy-path.** Not exercisable in an
   audit (can't place a real call). The gate works; the end-to-end call is unproven.
   **Place one real Aidan call in prod before claiming "AI recruiter calls candidates."**
3. **ainm.ai new-user flow.** Untouched and not tested (it's the LIVE client
   product). Owner should confirm its own signup still works.
4. **Mobile visual pass** at 375/1440 (built responsive; not pixel-verified here).
5. **AI cost exposure.** `/ai/generate-jd` and `/matches` (rerank) and now the AI
   Act narrative place real billed calls and are **not rate-limited** — a logged-in
   user can run up cost. Consider rate limits (out of scope for this fix run).

## Deferred as "needs separate run" (per instructions — NOT touched)

- **Transparency forgot-password bug** — `transparency-platform` has the same
  forgot-password issue; OUT OF SCOPE this run, needs its own run.
- **Governance / comply.ainm.ai AI Act overlap** — a second EU AI Act product
  overlapping execflex `/ai-act`; the owner decides its fate separately. Not touched.
- **hr-advisory-agent (ainm.ai)** — the LIVE client product (on branch
  `mobile-ux-signin` with uncommitted work) — deliberately not touched.

## Repo state

| Repo | Branch | HEAD | Deployed |
|---|---|---|---|
| execflex-backend | main | `d96d039` | Render (agentic-core v0.18.1) |
| execo-bridge | main | (bundle `index-DSw3V1vm.js`) | Hetzner, HTTP 200 |
| agentic-core | main | `v0.18.1` tagged | consumed via pin |
