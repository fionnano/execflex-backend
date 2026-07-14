# SHIPPED — ainm Marketplace MVP (run of 2026-07-14)

A demoable, curated two-sided marketplace for pre-vetted AI & data leaders, built
as a NEW `/marketplace` surface inside the existing estate. Separate from the
ainm Search `/console` recruiter product and from ainm.ai — reuses the org-scoped
auth, Supabase, and agentic-core; touches none of the other products.

Decisions logged in DECISIONS.md (D-14…D-16). Vetting methodology in
VETTING_METHODOLOGY.md.

## What's live

**Frontend — https://execflex.ai/marketplace (Hetzner, bundle `index-CGYk3Gd0.js`)**
- Deployed via deploy.sh (.env.local-aside pattern); live bundle hash matches
  local; HTTP 200. Verified the deployed JS contains the marketplace code
  ("Independently vetted", "marketplace/leaders", "Request introduction",
  "Get Vetted", "Vetted AI").
- Four routes, all behind the org-scoped auth, in the ainm dark shell
  (navy/emerald/cream, Fraunces), separate `MarketplaceLayout` nav:
  - `/marketplace` — browse the vetted pool, filter by skill / discipline /
    engagement.
  - `/marketplace/leaders/:id` — leader profile with the "Independently vetted"
    badge, the vetting rationale + per-competency bars, and **Request
    Introduction** (placement-fee terms + live fee estimate).
  - `/marketplace/apply` — supply side: create a profile → take the structured
    assessment → verified/rejected result.
  - `/marketplace/introductions` — operator pipeline with status transitions and
    placement-fee economics (realised + pipeline fees).

**Backend — https://execflex-backend-1.onrender.com/api/v1/marketplace (Render)**
- `services/marketplace/*` + `routes/api_v1/marketplace.py`, registered under the
  existing `api_v1_bp`. Org-scoped (`require_org`).
- 28 new tests (15 vetting scoring-path + 13 route), full suite **269 pass / 1
  skip**. Zero real LLM calls in tests; synthetic data only.

**Data — seeded into prod Supabase**
- 15 synthetic leaders (13 verified + 2 pending, across ML platform / data eng /
  AI product / applied research), 6 opportunities, 6 companies (with open roles),
  5 introductions in varied states (requested → hired). One command:
  `python scripts/seed_marketplace.py` (idempotent; writes via the service key).

## Live end-to-end verification (2026-07-14, against prod)

Ran the full journey as a brand-new synthetic user (admin-created → password
login → org-scoped JWT `role=owner`), against the deployed backend:
- **Browse:** 13 verified leaders returned; filters live (`track=ml_platform`→4,
  `engagement=fractional`→7).
- **Profile:** vetting badge, score, and rationale served.
- **Request Introduction:** created a `requested` intro (fee_pct 15).
- **Mark hired:** placement fee computed **€27,000 = 15% of €180,000**.
- **Pipeline summary:** totals + realised fees aggregated correctly.
- **Supply side:** apply created a `pending` leader; submitting the assessment
  **ran the real AI path live** (`ai_generated=true` — Render has agentic-core +
  the Anthropic key; Haiku scored answers, Sonnet wrote the rationale). It scored
  a deliberately low-effort submission (the same technical answer pasted into all
  six questions, including the two leadership questions) at 61/100 and correctly
  **rejected** it — evidence the vetting discriminates rather than rubber-stamps.
- All synthetic test artifacts (intro, applicant, auth user) were deleted
  afterwards; the seed pool (15 leaders / 5 intros) is intact. Zero real data.

Frontend: deployed bundle `index-CGYk3Gd0.js` on execflex.ai serves the
marketplace code (verified by content grep); routes render behind org auth.

## The vetting flow (the moat)

A leader picks a track and answers a fixed set of six questions (4 technical + 2
leadership, identical order per track — a fairness control). Scoring:
- **AI path (prod default):** per-answer mechanical scoring → **Haiku**
  (`claude-haiku-4-5`); overall explainable rationale + pass/fail → **Sonnet**
  (`claude-sonnet-4-5`), matching agentic-core's ModelRouter policy. Every AI
  decision is logged to `ai_decision_log` (EU AI Act Art. 13).
- **Deterministic fallback:** when the AI path is off/unavailable, a transparent
  heuristic (specificity, quantified evidence, concreteness, ownership) scores it
  — so the demo and tests never depend on live tokens.
- Pass threshold **70/100** → `vetting_status=verified`, badge **"Independently
  vetted"**. No "top X%" claim anywhere. A human confirms verification (Art. 14).

## The placement-fee model (represented, not charged)

Billing is placement-fee only — **no subscription**. When a company requests an
introduction and later marks it **hired**, the placement fee is computed as
**15% of first-year compensation** (configurable per intro) and shown in the
operator pipeline (per-intro fee + realised/pipeline totals). **No live Stripe
charge** — the economics are modelled and displayed. Live payment capture is a
later integration (below).

## Storage note (why no new tables tonight)

There is no autonomous path to apply DDL to the prod Supabase (no DB password, no
`exec_sql` RPC, no management token; prior migrations were applied by a human via
the dashboard). Rather than block, the MVP persists on the existing durable,
org-scoped tables under a namespace (DECISIONS.md D-14): leaders in
`people_profiles` (org=MARKETPLACE_ORG_ID; marketplace fields in
`source_metadata`), companies+roles in `opportunities` (`metadata.marketplace`),
introductions in `activity_log` (`entity_type='placement'`). This reuses the
estate and is fully durable — verified end-to-end against prod. A clean
dedicated-tables migration (`supabase/migrations/20260714_marketplace.sql`) is
committed and idempotent, ready to graduate onto its own tables when a human
applies it.

## Still needs a human — ranked

1. **Live payment capture.** The placement-fee economics are modelled and
   displayed; wiring Stripe (invoice/charge on "hired") is not done tonight.
2. **A real proctored assessment tool.** Vetting is a structured written
   assessment. The assessment-adapter seam
   (`execo-bridge/src/lib/assessment-adapter.ts`) is preserved so a live
   proctored technical test can plug in as an additional signal.
3. **Real supply & demand.** The pool is 15 synthetic leaders. Real onboarding of
   vetted leaders and real company demand is the next step.
4. **Graduate onto dedicated tables.** Apply `20260714_marketplace.sql` in the
   Supabase dashboard and point `services/marketplace/store.py` at the dedicated
   tables (currently namespaced over existing tables).
5. **Per-tenant demand-side scoping.** The operator introductions pipeline is
   marketplace-wide in this MVP (`?scope=mine` narrows to the caller's org). Full
   multi-tenant demand-side isolation is a later step.
6. **Companies view shows 6, not 8.** Companies are derived from opportunities, so
   only the 6 with open roles surface; 2 seed companies have no open role. Cosmetic.

## Repo state

| Repo | Branch | Deployed |
|---|---|---|
| execflex-backend | main (`6a7f88c`) | Render — /api/v1/marketplace |
| execo-bridge | main (`5e4eb14`) | execflex.ai/marketplace (bundle `index-CGYk3Gd0.js`) |
