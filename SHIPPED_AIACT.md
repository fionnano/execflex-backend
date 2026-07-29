# SHIPPED — ainm AI Act Check (run of 2026-07-29)

A live EU AI Act **readiness / decision-support** product a company uses to assess
its own AI use — especially AI in hiring/HR — for EU AI Act risk. Practitioner-built,
EU AI Act aware: it returns a risk tier, the obligations you're subject to (article
by article), your gaps, and a readiness score with an explainable rationale.

**Not legal advice.** The disclaimer is returned by the API (on the question set
and every result) and rendered in the product UI.

New surface only. Untouched: ainm.ai, transparency, ainm Search `/console` core,
and the marketplace's core flows. Decisions in DECISIONS.md **D-18**.

---

## Live verification (2026-07-29, against prod) — 12/12 passed

One end-to-end run against the deployed backend with **real Supabase auth
accounts** (created via the admin API, org context from the live JWT hook),
covering both org tenants, then every synthetic artifact deleted. Reproducible:
`scripts/verify_aiact.py`.

| Check | Result |
|---|---|
| Two org-scoped accounts provisioned (JWT `org_id`) | ✅ |
| Question set live — **4 stages + disclaimer** (confirms agentic-core **v0.18.0** reinstalled on Render) | ✅ |
| High-risk hiring AI → **High Risk**, score 15, "Significant gaps identified" | ✅ |
| **11 obligations** mapped incl. Article 9/14 + Annex III(4) employment | ✅ |
| **5 gaps** + recommendations produced | ✅ |
| Disclaimer attached to the result | ✅ |
| Result **persists** (status=scored) | ✅ |
| Prohibited practice (social scoring) → **Unacceptable Risk** (hard stop) | ✅ |
| **Tenant isolation** — org B cannot see org A's assessment (404 + empty list) | ✅ |
| GDPR **export** returns the full assessment | ✅ |
| GDPR **delete** erases the assessment | ✅ |
| **AI narrative path live** — `ai_generated=true`, agentic-core scoring_engine / Sonnet | ✅ real LLM |

Frontend live at **https://execflex.ai/ai-act** (Hetzner, bundle
`index-ByVBwdik.js`, HTTP 200) — content-verified to contain the new UI
("EU AI Act Check", "readiness report", "AI-generated", "Your obligations",
"Prohibited practice detected", "/ai-act").

Backend live at **https://execflex-backend-1.onrender.com/api/v1/aiact** (Render).

---

## What's live and REAL

**The shared engine (Phase 1 — agentic-core v0.18.0).** The compliance engine was
already ported into agentic-core (v0.17.0: prohibited_practices, snapshot_scorer,
scoring_engine, risk_summary, snapshot_gaps, all on SingleStepAgent + routing +
structured logging). This run **added two deterministic units** (additive; no
existing agent touched):
- `question_set.py` — the staged intake (intake → prohibited → scope → governance),
  hiring/HR-focused, stable ids that map to the engine inputs.
- `obligations.py` — a deterministic risk-tier → obligation mapper: given a
  classification + context, it returns the concrete Articles with a plain
  requirement and a practical deployer action.

Tagged **v0.18.0**, pushed; execflex pins `agentic-core @v0.18.0`. Compliance suite
**138 pass / 6 async-skipped**. The multi-consumer gate is honoured — additive only,
no existing symbol changed, so transparency's and execflex's suites are unaffected.

**The product (Phase 2).** `services/aiact` in execflex-backend orchestrates a
**deterministic backbone** (Article 5 prohibited screen + snapshot score +
rule-based classification + obligation mapping + gap analysis) that is
token-free, explainable, and defensible for a compliance tool. An **optional
AI-generated narrative** (agentic-core scoring_engine / Sonnet) is added behind
`AIACT_AI` and **always marked `ai_generated`**, with the deterministic rationale
as fallback. Classification/obligations/gaps are deterministic (not AI) by design.
Verified live: both the deterministic and the AI path work against prod.

**The UI.** `/ai-act` (execo-bridge): a staged questionnaire (progress bar,
per-stage Back/Next, required-field gating) → a readiness report (risk-tier badge,
readiness score, decision, prohibited alert, obligations with article + deployer
action, gaps, recommendations, GDPR export/delete, re-take). AI-generated summary
and recommendations carry a visible **"✨ AI-generated"** marker; the disclaimer
appears on the question set and every result. Built **mobile-first** (single
column, `sm:` breakpoints) for 375px and 1440px.

**Tie-in to the suite (Phase 3).** An **"EU AI Act Check"** entry point in the
marketplace nav and an **"AI Act Check"** item in the console's Compliance group.
Methodology documented in **AI_ACT_METHODOLOGY.md**.

**Safe for real users (Phase 4).** Tenant isolation (every read org-scoped —
verified live); `validation.py` validates every answer against the question set's
allowed options; scoring endpoint rate-limited (per-IP 20/h + per-org 40/h); GDPR
export + delete. AI narrative decisions logged to `ai_decision_log`
(decision_type=`ai_act_readiness`).

**Tests.** +12 backend route tests (classification across all tiers, obligations,
gaps, tenant isolation, GDPR, validation, rate-limit) + 26 agentic-core tests.
Full execflex suite **301 pass / 1 skip**.

---

## Storage & the single human step

No new prod DDL was applied (same known constraint — no DB password / management
token). Assessments persist on the existing durable `activity_log` table under a
namespace: `entity_type='client'`, `activity_type='ai_act_assessment'`,
`metadata.aiact=true`, owned by the creating org. **This is what the live,
verified product uses.**

**`AI_ACT_MIGRATION.sql`** (repo root) is the graduation path: a dedicated
`aiact_assessments` table **+ an RLS tenant policy**. Optional for launch. To
graduate: paste it into the Supabase dashboard SQL editor, then repoint
`services/aiact/store.py` at the table (response shapes already match).

---

## What's stubbed / seed / not done (honest)

- **No live payments.** This surface has no billing; it's a trust/readiness tool.
  (If it later becomes a paid product, Stripe wiring is net-new.)
- **AI narrative is off by default** (`AIACT_AI`) to keep scoring instant and
  token-free; the deterministic path is the default and is fully real. The AI path
  is verified working live when enabled.
- **RLS ships as SQL to apply** with the dedicated table; today the service-role
  backend enforces tenant scoping in code (verified live).
- **Responsive built, not pixel-screenshotted.** The pages are mobile-first with
  responsive Tailwind classes and the site returns 200; a final human visual pass
  at exactly 375px and 1440px is a recommended check (see below).
- **Methodology is practitioner-built, not lawyer-reviewed.** It is framed as
  readiness/decision-support, not legal advice, in the product and the doc.

---

## Ranked human follow-ups

1. **Legal review of the methodology & obligation mapping.** The article mapping is
   careful and practitioner-built, but a qualified EU AI Act lawyer should review
   `obligations.py` and `AI_ACT_METHODOLOGY.md` before this is leaned on
   externally. Highest priority for a compliance-adjacent product.
2. **Drive real users.** The tool is ready and tied into the console + marketplace;
   getting real companies to run it is the next step.
3. **Final responsive visual pass** at 375px and 1440px, and a quick a11y check.
4. **Apply `AI_ACT_MIGRATION.sql`** (dedicated table + RLS) — defence-in-depth and
   a cleaner schema; the backend already enforces the rules in code.
5. **Decide AI-default.** Consider enabling `AIACT_AI` in prod for the richer
   narrative (verified working), weighing per-assessment token cost vs. UX.

---

## Repo state

| Repo | Branch | Deployed |
|---|---|---|
| agentic-core | main (`v0.18.0`) | consumed by execflex via git pin |
| execflex-backend | main (`c3405ca`) | Render — /api/v1/aiact (live) |
| execo-bridge | main (`8974c5d`) | execflex.ai/ai-act (bundle `index-ByVBwdik.js`) |
