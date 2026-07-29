# SHIPPED — Dedicated-table repoint + Unified suite shell (run of 2026-07-29)

Two goals, both to completion, deployed and live-verified. **The live client
product ainm.ai was not touched and is confirmed working.**

Decisions: DECISIONS.md **D-19** (repoint) and **D-20** (auth reality + shell).

---

## Live verification (2026-07-29, against prod) — 11/11 passed

One end-to-end run against the deployed backend with **real Supabase auth
accounts**, then all synthetic artifacts deleted. Reproducible:
`scripts/verify_unified.py`.

| Check | Result |
|---|---|
| Two org-scoped accounts provisioned | ✅ |
| Leader created + vetted live (Haiku+Sonnet, 74/100 verified) | ✅ |
| Leader row lands in the **DEDICATED** `marketplace_leaders` table | ✅ |
| Leader is **NOT** in the namespaced `people_profiles` (repoint proven) | ✅ |
| Introduction lands in the **DEDICATED** `marketplace_introductions` table | ✅ |
| Marketplace browse serves the **migrated** pool (13 verified seed) | ✅ |
| AI Act assessment scored (High Risk) | ✅ |
| Assessment lands in the **DEDICATED** `aiact_assessments` table | ✅ |
| Suite returns 5 modules — internal one-login + external separate-login | ✅ |
| **ainm.ai up and serving (HTTP 200, unchanged)** — not regressed | ✅ |
| transparency.ainm.ai reachable | ✅ |

Post-deploy smoke, all healthy: backend `/health` 200; `/marketplace/facets`,
`/aiact/questions`, `/suite/modules` all 401 (auth-gated, live); execflex.ai
`/suite`, `/marketplace`, `/ai-act`, `/console` all 200; **ainm.ai 200**.

---

## GOAL A — now on clean dedicated tables

Both products graduated off the namespaced `activity_log`/`people_profiles`
workaround onto their dedicated tables (already applied in prod):

- **Marketplace** → `marketplace_leaders`, `marketplace_companies`,
  `marketplace_opportunities`, `marketplace_introductions`,
  `marketplace_vetting_assessments`. `services/marketplace/store.py` rewritten;
  public signatures + serialized shapes unchanged (routes/frontend/tests
  untouched). Leader↔account link now uses the real `user_id` column (UNIQUE) —
  the `source_metadata.owner_user_id` workaround is gone.
- **AI Act** → `aiact_assessments`. `services/aiact/store.py` rewritten,
  tenant-scoped, GDPR intact.

**Schema deltas found at migrate time** (the applied SQL differed slightly from
the generated file): `marketplace_introductions` has no `leader_name` column
(resolved from the `leader_id` FK at read time) and `marketplace_opportunities`
has no `updated_at`. The store was adapted to the **real** prod schema — verified
by reading every dedicated table live.

**ON DELETE fix (in code):** `delete_leader` removes the leader's introductions
first (the `leader_id` FK is RESTRICT), then the leader (vetting assessments
cascade). The GDPR erase path now works for a leader with existing introductions
(+1 test, and exercised live).

**Migration:** `scripts/migrate_marketplace_to_dedicated.py` (idempotent, in-code)
copied the namespaced rows into the dedicated tables — **15 leaders / 7 companies
/ 6 opportunities / 5 introductions**. AI Act had no rows to migrate.

**Reversibility:** the namespaced source rows were **left intact**. If the repoint
ever misbehaves, reverting the backend deploy makes it read the old store again —
no data was destroyed. Migration ran **before** the deploy, so there was zero
serving gap.

Tests: full execflex suite **306 pass / 1 skip** (+ the leader-with-introductions
GDPR delete test).

---

## GOAL B — unified suite shell (one experience, modular access)

### The auth reality (detected & logged — this drove everything)

- **execflex.ai** — Search (`/console`), Marketplace (`/marketplace`), AI Act
  Check (`/ai-act`) are all one SPA (execo-bridge) on **one Supabase project**.
  These three **already share a single login** — moving between them is seamless.
- **ainm.ai (hr-advisory-agent)** — a **separate app** with its **own custom JWT
  auth and its own Postgres** (no Supabase). It is the **LIVE client product**
  (Republic of Work). **transparency.ainm.ai** is likewise separate.

So execflex and ainm.ai/transparency are **SEPARATE auth/accounts**. Per the hard
rule, there was **no user-DB merge and no change to ainm.ai's auth**.

### What was built (purely additive — no product internals changed)

- **Backend** `services/suite` + `GET /api/v1/suite/modules`: a config-driven
  module registry that returns the caller's entitled modules. Internal modules
  (search/marketplace/aiact) are one-login; external modules (hr → ainm.ai,
  transparency → transparency.ainm.ai) are marked `external` + `separate_login`
  with their URL.
- **Frontend**: **SuiteHome** (`/suite`) is the new post-login front door — a
  module dashboard. A **SuiteSwitcher** dropped into every module's top bar
  (Console / Marketplace / AI Act) lets a logged-in user jump between modules.
  Internal modules navigate in-app; external ones open in a new tab, clearly
  labelled **"Separate sign-in"**. Navy/emerald/Fraunces, mobile-first.
- Default post-login redirect changed from `/console` → `/suite`; `/console` is
  unchanged and fully reachable.

### Real SSO vs. shell-only (honest)

- **Real single sign-on:** across the **three internal execflex modules** (Search,
  Marketplace, AI Act) — same Supabase session, genuinely one login.
- **Shell-linked (separate sign-in):** **HR (ainm.ai)** and **Transparency**. They
  appear as first-class modules in the suite home and switcher, but opening them
  is a deep link into a separate app with its own login. A full cross-product SSO
  or account-link would require modifying ainm.ai's auth, which is **out of scope
  by the hard rule** (live client product). No bridge token is injected; nothing
  in ainm.ai was changed.

### Module entitlements status

Config-driven and **real/demoable**, not a billing integration:
- Default: all 5 modules entitled. Restrict per org via `SUITE_ORG_MODULES`
  (JSON `{org_id: [keys]}`) or globally via `SUITE_DEFAULT_MODULES`. External URLs
  overridable via `SUITE_URL_HR` / `SUITE_URL_TRANSPARENCY`.
- The endpoint returns only entitled modules by default (`?all=1` returns the full
  registry with an `entitled` flag for an upsell/admin view). Verified live (5
  modules) and in tests (per-org restriction narrows the set).

---

## ainm.ai NOT regressed — explicit confirmation

ainm.ai and transparency.ainm.ai were **never modified** (different repos,
different auth, different DB). Confirmed live at the end of the run: **ainm.ai
returns HTTP 200 and serves its content** ("Ainm — Ireland's AI-Native HR
Platform"); transparency.ainm.ai reachable (200). The unified shell only **links
out** to them.

---

## What's still stub / seed / not done (honest)

- **No cross-product SSO to ainm.ai/transparency** — shell-linked with separate
  sign-in by design (safe). Real SSO is a future opt-in that must be done from the
  ainm.ai side.
- **Entitlements are config/env-driven**, not billing — enough to be real and
  demoable; a real plan/billing gate is future work.
- **Responsive** built mobile-first (single-column, `sm:`/`lg:` breakpoints,
  mobile-safe switcher panel) and the live pages return 200; a final pixel pass at
  exactly 375px and 1440px is a recommended human check.
- **Namespaced marketplace rows left in place** (intentionally, for reversibility)
  — they can be cleaned up once the dedicated tables are trusted in prod for a
  while.

---

## Ranked human follow-ups

1. **Watch prod on the dedicated tables**, then (optionally) delete the leftover
   namespaced marketplace rows in `people_profiles`/`opportunities`/`activity_log`
   once confident (reversibility no longer needed).
2. **Real SSO for HR/Transparency** — the only way to make ainm.ai truly one-login
   is a bridge on the ainm.ai side (accept an execflex-issued token, or a shared
   identity provider). Deliberately deferred to protect the live product.
3. **DB-level `ON DELETE` on `marketplace_introductions.leader_id`** — currently
   handled in code; an `ALTER … ON DELETE CASCADE` (or `SET NULL` + nullable) would
   make it belt-and-braces. Generated SQL can be added alongside the migration.
4. **Real entitlements/billing** — wire module access to plans when there's a
   commercial reason.
5. **Full codebase merge** — not recommended now; the shell gives "one suite"
   without the risk of merging a live product's codebase.

---

## Repo state

| Repo | Branch | Deployed |
|---|---|---|
| execflex-backend | main (`b26657b`) | Render — dedicated-table stores + /api/v1/suite |
| execo-bridge | main (`92b1d5b`) | execflex.ai — /suite + module switcher (bundle `index-CwjJDDjk.js`) |
| agentic-core | main (`v0.18.0`) | unchanged this run |
| hr-advisory-agent (ainm.ai) | **untouched** | ainm.ai — live, confirmed 200 |
