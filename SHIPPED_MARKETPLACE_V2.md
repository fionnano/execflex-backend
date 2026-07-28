# SHIPPED — ainm Marketplace v2 (run of 2026-07-28)

The `/marketplace` surface is now a **real, functional two-sided marketplace**
that actual leaders and companies can sign up to and use — not a synthetic demo.
Seed data stays for richness; every flow is real, on real auth accounts, with
real persisted state. Verified live end-to-end against production.

Separate from ainm Search `/console`, ainm.ai, transparency, and governance —
none of those were touched. Builds on the MVP (SHIPPED_MARKETPLACE.md); decisions
in DECISIONS.md **D-17**.

---

## Live verification (2026-07-28, against prod)

Ran the full two-sided journey with **two brand-new REAL Supabase auth accounts**
(a leader and a company, created via the admin API, org context provisioned by
the live signup hook) against the deployed backend. **17/17 checks passed**, then
every synthetic artifact was deleted. Reproducible: `scripts/verify_marketplace_v2.py`.

| Check | Result |
|---|---|
| Org provisioning sets `org_id`/`role` in the JWT (both accounts) | ✅ |
| Leader profile created and **linked to the auth account** | ✅ |
| `GET /me` reports the caller is a leader with their profile | ✅ |
| **Live AI vetting** (Haiku per-answer + Sonnet rationale) verified a strong candidate **74/100** | ✅ real LLM path |
| Verified leader appears in **ranked search** with match reasons (rank 2, "Skills: MLOps, Feature Stores…") | ✅ |
| Search **never** exposes contact details | ✅ |
| Company profile saved; introduction created (`requested`, fee 15%) | ✅ |
| Leader contact **hidden** to the company pre-acceptance | ✅ |
| Leader inbox shows the request with the company's identity | ✅ |
| Leader **accepts** → contact **revealed** to the company | ✅ contact reveal |
| Mark hired → placement fee **€30,000 = 15% of €200k** | ✅ |
| **Tenant isolation**: a different org sees none of the company's introductions | ✅ |
| GDPR **export** returns the full data bundle; **delete** erases the profile | ✅ |

Frontend live at **https://execflex.ai/marketplace** (Hetzner, bundle
`index-BvbA3PRP.js`, HTTP 200) — deployed bundle content-verified to contain the
new UI ("Semantic matching", "My Introductions", "Requests", "match_reasons",
"Company Profile", "contact_revealed", "marketplace/inbox").

Backend live at **https://execflex-backend-1.onrender.com/api/v1/marketplace**
(Render, auto-deployed from `main`).

---

## What's live and REAL

**Real accounts.** Signup is the existing Supabase magic-link flow at `/auth`; the
`handle_new_user` hook provisions `org_id`/`role` into the JWT (confirmed live).
Every marketplace call is org-scoped off that token.

**Supply side (leaders) — all real:**
- Create a profile **linked to your account** (`POST /marketplace/leaders`,
  idempotent claim — one profile per account), edit it (`PATCH`), see it at `/me`.
- Take the **vetting assessment** (`POST …/vetting`) — verified/rejected with an
  explainable rationale, per-competency scores, and an `ai_decision_log` audit
  entry. AI path (Haiku+Sonnet) runs live on Render; deterministic fallback when
  the key/flag is off.
- Appear in **search** once verified; edit profile; `GET /marketplace/inbox` to
  see who requested an introduction; **accept/decline** (`POST …/respond`).
- **GDPR**: `GET /marketplace/me/export` (full data bundle) and
  `DELETE /marketplace/me` (erase). Email on vetting result and intro request.

**Demand side (companies) — all real:**
- Search the pool (`GET /marketplace/search`), view a verified profile + vetting
  rationale, save a **company profile** (`GET/PUT /marketplace/company`) so
  leaders see who's asking.
- **Request an introduction** (real `activity_log` row, placement-fee terms shown
  and agreed), **track** introductions (`GET /marketplace/introductions`,
  tenant-scoped to your org), **record an outcome** (interviewing / hired / not
  proceeding). Placement fee computed and shown on hire.

**The search marketplace (Phase 2).** `services/marketplace/search.py` ranks the
verified pool by lexical relevance across headline / skills / sectors / bio /
seniority / discipline, combined with structured facets (skill, discipline,
seniority, engagement, sector, comp range). Results are **ranked, not just
filtered**, and each carries **why it matched**. Empty query ranks by vetting
score. An **agentic-core Sonnet semantic re-rank** is available behind
`MARKETPLACE_SEARCH_AI` (or the per-request `?ai=1` / UI toggle) for queries like
"someone who's scaled a data platform in fintech" — it degrades to the lexical
ranking on any failure. `GET /marketplace/facets` feeds the UI chips.

**Safe for real users (Phase 5).**
- **Contact privacy**: a leader's email/phone/LinkedIn are revealed to a company
  **only after the leader accepts** that company's introduction — never browsable,
  never in search.
- **Tenant isolation**: `GET /introductions` returns only the caller's org's
  introductions; the all-tenants operator pipeline is a separate admin-gated
  endpoint (`GET /admin/introductions`).
- **GDPR**: export + erase (above).
- **Rate limiting**: the vetting endpoint is guarded per-IP (8/h) and per-leader
  (3/day) against assessment farming, and is owner-gated.
- **Input validation**: `services/marketplace/validation.py` validates every
  public form (lengths, types, enums, email/URL).
- **RLS**: policies are written in `MARKETPLACE_MIGRATION.sql` (applied with the
  dedicated tables — see below).

**Tests.** +20 new (ranking, account linkage, contact reveal, tenant scoping,
GDPR, validation, rate-limit). Full suite **289 pass / 1 skip**. Zero real LLM
calls in tests; synthetic data only.

**Seed.** Refreshed in prod with the v2 seeder: 15 synthetic leaders (with
synthetic `example.com` contact for the reveal demo), 6 opportunities, 5
introductions in varied states with contact snapshots. `python
scripts/seed_marketplace.py` (idempotent, service key).

---

## The single human step — apply the migration (optional for launch)

`MARKETPLACE_MIGRATION.sql` (repo root) creates the dedicated marketplace tables
(`marketplace_leaders/_companies/_opportunities/_introductions/_vetting_assessments`)
**plus RLS policies**, reflecting the exact v2 shapes and privacy/tenant rules.

- **It is NOT required to run today.** The marketplace runs fully on the existing
  durable tables under a namespace (D-14/D-17): leaders in `people_profiles`
  (`org = MARKETPLACE_ORG_ID`, owner in `source_metadata.owner_user_id` — the
  `user_id` column is globally unique and reserved by the onboarding hook),
  company profiles + introductions in `activity_log`. This is what the live,
  verified product uses.
- **To graduate**: paste the file into the Supabase dashboard SQL editor
  (project `krzacydualjpsapffpfm`), then repoint `services/marketplace/store.py`
  at the dedicated tables — the API response shapes are already identical, so no
  route changes are needed. There is still no autonomous DDL path to prod
  (no DB password / management token), which is why this stays a human step.

---

## Still needs a human — ranked

1. **Live Stripe / payment capture.** Placement-fee economics are modelled,
   computed on hire, and displayed. Charging the fee (invoice/charge on "hired")
   is not wired. Highest-value next step to actually collect revenue.
2. **Drive real users.** The product is ready; onboarding real vetted leaders and
   real company demand is a go-to-market activity, not an engineering gap. (The
   pool shown is 15 synthetic leaders until real ones join.)
3. **Apply the dedicated-tables migration + RLS** (above). Defence-in-depth and a
   cleaner schema; the service-role backend already enforces every rule in code.
4. **A real proctored assessment tool.** Vetting is a structured written
   assessment scored by AI with human oversight. The assessment-adapter seam
   (`execo-bridge/src/lib/assessment-adapter.ts`) is preserved so a live proctored
   test (HackerRank/Codility/live interview) can plug in as an additional signal.
5. **Email deliverability.** Notifications (vetting result, intro request,
   accept/decline) send via the existing Gmail SMTP path (`modules/email_sender.py`)
   and are best-effort (log-and-continue if unconfigured). For volume, move to a
   transactional provider (SES/Postmark) and add SPF/DKIM.
6. **Semantic search at scale.** The lexical ranker is fast and explainable; the
   Sonnet re-rank is behind a flag and re-ranks only the top candidates. For a
   large pool, add embeddings/pgvector for first-pass recall.

---

## What's still seed or stub (honest)

- **Leader pool** is 15 synthetic leaders (+ any real signups). Their contact is
  synthetic `example.com`.
- **Placement fee** is computed and displayed but **not charged** (no live Stripe).
- **AI semantic re-rank** is **off by default** (flag/toggle) to avoid per-search
  token cost; lexical ranking is the default and fully real.
- **RLS** ships as SQL to apply with the dedicated tables; today the service-role
  backend enforces the same rules in code (verified live).
- **Operator admin pipeline** (`/admin/introductions`) is gated by operating as
  the marketplace org or an env allowlist (`MARKETPLACE_ADMIN_USER_IDS`).

---

## Repo state

| Repo | Branch | Deployed |
|---|---|---|
| execflex-backend | main (`4dfa702`) | Render — /api/v1/marketplace (v2 live) |
| execo-bridge | main (`520449e`) | execflex.ai/marketplace (bundle `index-BvbA3PRP.js`) |
