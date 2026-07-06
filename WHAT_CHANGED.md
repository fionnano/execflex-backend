# What Changed — ExecFlex v1 Rebuild

Everything that changed from the pre-rebuild state, tagged by status.

**Tags:**
- `[VISIBLE NOW]` — live in the local dev build, clickable in the browser
- `[NEEDS DEPLOY]` — code is written but requires merging to main and deploying
- `[FLAG-GATED]` — behind an `EXECFLEX_AI_*` feature flag (all ON in local dev)

---

## Frontend — Screen by Screen

### Agency Dashboard (`/agency`) `[VISIBLE NOW]`
- **New page.** Stat cards showing Active Jobs, Total Candidates, Pending Reviews, Pipeline count.
- Recent Jobs card with status badges and pay ranges.
- Pending AI Reviews card with blue-highlighted AI decision cards — Sparkles icon, score percentage, explanation text, review link. `[FLAG-GATED]`
- Navigation buttons to Jobs, Pipeline, Compliance Centre.

### Jobs List (`/agency/jobs`) `[VISIBLE NOW]`
- **New page.** Table view of all agency jobs with status filter (all/open/draft/closed/paused).
- Columns: Title, Location, Industry, Pay Range (EUR/GBP/USD formatted), Status badge, Created date, Edit action.
- Pagination (sets of 20).

### Job Form (`/agency/jobs/new`, `/agency/jobs/:id/edit`) `[VISIBLE NOW]`
- **New page.** Full job creation/editing form: title, description, location, industry, commitment type, skills, experience range.
- Pay range fields are **mandatory** (min + max with currency/period dropdowns) — EU Pay Transparency Directive enforced at API layer. `[VISIBLE NOW]`
- "Generate with AI" button produces a job description using the JD Generator agent, with word count and gender-neutral language flags. `[FLAG-GATED]`
- Post-creation syndication panel: select boards (LinkedIn, Indeed, IrishJobs, Google) and submit. `[VISIBLE NOW]`

### Pipeline Board (`/agency/pipeline`) `[VISIBLE NOW]`
- **New page.** Kanban board with 8 columns: sourced → screened → shortlisted → interviewing → offered → placed → rejected → withdrawn.
- Candidate cards show name, location, time-in-stage badge, move dropdown.
- Rejection/withdrawal require reason in a dialog (human review gate — D-17). `[VISIBLE NOW]`
- Job filter dropdown.

### Candidate Profile (`/agency/candidates/:id`) `[VISIBLE NOW]`
- **New page.** Header with name, current stage badge, contact info.
- **Screening tab:** Transcript with screening decisions. AI Screening Summary card (blue, Sparkles icon, "AI-Generated" badge, EU AI Act advisory note). `[FLAG-GATED]`
- **Match Scores tab:** Match results with progress bars. AI Match Analysis card (advisory, not authoritative — deterministic score remains the decision basis per D-28). `[FLAG-GATED]`
- **Pipeline History tab:** Timeline of stage transitions with reasons.
- **Actions tab:** Move candidate to another stage.

### Screening Review (`/agency/screening-review`) `[VISIBLE NOW]`
- **New page.** AI decision review queue per EU AI Act Art. 14.
- 3 stat cards: Total Screenings, Pending Review, Reviewed Today.
- Pending decisions as blue cards with Sparkles icon and "AI-Generated" badge — shows score, explanation, candidate name.
- Approve / Override buttons. Override dialog requires new recommendation + reason. `[VISIBLE NOW]`
- Reviewed section (collapsible) showing approved vs overridden decisions with audit trail.

### Compliance Centre (`/agency/compliance`) `[VISIBLE NOW]`
- **New page.** Three tabs:
  1. **AI Decisions** — Table of all AI-generated decisions (source, type, candidate, score, explanation, reviewed status, date). Filter by type and unreviewed-only. Review dialog. `[VISIBLE NOW]`
  2. **Data Rights** — GDPR data rights requests table (type, name, email, status, date). Process dialog for pending requests. Public-facing submission (no auth required). `[VISIBLE NOW]`
  3. **AI Act Snapshot** — 5-question self-assessment form (uses AI, business functions, affects people, in EU, has documentation). Result shows risk score (red/amber/green), headline, recommendations, AI-identified gaps with Sparkles badge. `[FLAG-GATED]`

### Talent Pools (`/agency/talent-pools`) `[VISIBLE NOW]`
- **New page.** Grid of talent pool cards (name, description, member count, verified badge).
- Expand to see members table (candidate, verified status, assessment score, provider, date).
- Create Pool / Add Member dialogs.

### Public Pages (unchanged)
- Landing page (`/`), Auth (`/auth`), Pricing (`/pricing`), Find Jobs (`/find-jobs`), Find Talent (`/find-talent`), Role Detail (`/role/:id`), Resources (`/resources`), Candidate Landing (`/join`), Client Landing (`/hire`) — all unchanged from pre-rebuild.

### Legacy Pages (still in code, routes still wired)
- Executive Onboarding (`/my-profile`), Post Role (`/post-role`), Match Detail (`/match/:id`) — pre-rebuild flows, still routed but superseded by agency pages. Tagged DEAD in ESTATE_MAP. `[NEEDS DEPLOY]` to remove.

---

## Backend — Capability by Capability

### v1 Multi-Tenant API (`/api/v1/*`) `[NEEDS DEPLOY]`
- **All new.** 11 route modules: jobs, candidates, applications, screens, matches, pipeline, syndication, compliance, talent_pools, ai. Every endpoint extracts org_id from JWT claims (D-16) — no request-body org_id accepted.
- 217 tests green.

### Matching Engine `[NEEDS DEPLOY]`
- **New.** 7-dimension weighted scoring (skills 0.25, industry 0.20, experience 0.15, location 0.10, availability 0.10, compensation 0.10, screening 0.10).
- NED penalty (×0.3), passive multiplier (×0.85), closed multiplier (×0.1).
- 42 tests, all deterministic — zero LLM calls.

### Screening State Machine `[NEEDS DEPLOY]`
- **New.** IDLE → CONSENT → INTAKE → SCORING → COMPLETE state machine.
- GDPR consent disclosure mandatory before any screening proceeds (D-05).
- Distress phrase handoff (lawyer, discrimination, harassment → immediate HANDOFF state).
- Heuristic scoring (length-based 1-5, placeholder for LLM scoring).
- 73 tests.

### Syndication Engine `[NEEDS DEPLOY]`
- **New.** Board adapters for LinkedIn XML, Indeed XML, IrishJobs XML, Google Indexing API (stub).
- Pay range included in all feeds (Pay Transparency Directive).
- 52 tests.

### Compliance Layer `[NEEDS DEPLOY]`
- **New.** AI decision log (inputs, model version, scores — Art. 12), human review gate (blocks all automated terminal decisions — D-17), data rights endpoint (public, no auth — GDPR Art. 15/17).
- 17 tests.

### Security Hardening `[NEEDS DEPLOY]`
- Smoke test bypass blocked in production via `FLASK_ENV`/`APP_ENV` guard (FIX-3).
- SECURITY_CLOSURE.md with 3 code fixes + 6 REQUIRES_HUMAN items.

---

## AI Capabilities — Agent by Agent

### Match Re-Rank Agent `[FLAG-GATED]`
- LLM re-ranks match results with reasoning. Advisory only — deterministic score stays authoritative (D-28).
- Sonnet tier (REASONING). Falls back to deterministic-only if LLM fails (D-33).

### Screening Summary Agent `[FLAG-GATED]`
- Produces structured assessment (strengths, gaps, flags, next_step) from screening transcript.
- Supplements heuristic scoring, doesn't replace it (D-29). Both shown to human reviewer.
- Sonnet tier (REASONING).

### CV Parser Agent `[FLAG-GATED]`
- Extracts structured candidate data from CV/resume text.
- Haiku tier (EXTRACTION) — mechanical extraction, doesn't need creative output.

### JD Generator Agent `[FLAG-GATED]`
- Generates job descriptions with gender-neutral language check.
- Sonnet tier (DRAFTING) — creative output needs quality control.
- Surfaced as "Generate with AI" button in JobForm.

### Question Flow `[FLAG-GATED]`
- Per-role configurable 5-question screening structures (general, technology, finance, executive).
- Pure data module — no LLM calls. Consumed by voice screening.

### Compliance Snapshot `[FLAG-GATED]`
- EU AI Act quick-check: 5-question self-assessment → risk score + gap analysis.
- Scorer is pure logic (always runs). Gaps agent uses Sonnet (only when flag ON).
- Surfaced in ComplianceCentre → AI Act Snapshot tab.

### Prohibited Practices Checker `[FLAG-GATED]`
- Deterministic Art. 5 checker — no LLM. Flags subliminal techniques, social scoring, real-time biometric.

---

## Under the Hood — Non-Visible Changes

### Feature Flag System `[VISIBLE NOW]`
- 6 env-var flags controlling all AI capabilities, all OFF by default in production.
- `GET /api/v1/ai/status` reports flag state.
- Local dev script sets all ON.

### ISO 27001 + 42001 Scaffold `[NEEDS DEPLOY]`
- 7 documents in `iso/`: Statement of Applicability (93 controls), Asset Register, Risk Register (20 risks), Incident Response, Change Management, AI Management System (ISO 42001), Gap List (28 items).
- Not user-visible — audit/certification preparation.

### governance-platform Overhaul `[NEEDS DEPLOY]`
- Model routing: Opus → Haiku (summaries) / Sonnet (scoring, chat).
- Haiku thinking guard: `_complete()` auto-drops thinking param for Haiku models.
- Compliance chat moved from Haiku to Sonnet (client-facing quality).
- PII sanitizer on all log output.
- 51 tests green on main.

### transparency-platform List-Numbering Fix `[NEEDS DEPLOY]`
- DFY pack PDFs now preserve original numbered list numbering and collect multi-paragraph items.
- On `defect-fixes` branch, not yet merged to master.

### Estate Consolidation Documents `[NEEDS DEPLOY]`
- ESTATE_MAP.md (6 codebases, all capabilities mapped with KEEP/FOLD/DEAD tags).
- PROD_CLEANUP.md (13 production hygiene findings).
- DECISIONS.md (D-01 through D-50).
- SUMMARY.md (uncertainty-ranked decisions for owner review).

---

## Test Counts

| Suite | Tests | Status |
|-------|-------|--------|
| ExecFlex backend | 217 | All pass |
| governance-platform | 51 | All pass (on main) |
| agentic-core | 736 | All pass (recruitment-agents branch) |
| transparency-platform | 24 DFY pack | All pass (defect-fixes branch) |
| **Total verified** | **1028** | |

---

## How to Run

```powershell
cd c:\Users\fionn\execflex-backend
.\start-local.ps1
```

Backend: http://localhost:5001 | Frontend: http://localhost:8080

**Demo mode** (`VITE_DEMO_MODE=true` in `.env.local`): bypasses auth and serves synthetic data — no Supabase queries, no real data. Navigate directly to `http://localhost:8080/agency` for the full rebuilt console. 12 synthetic candidates, 5 jobs, 5 AI decisions, 2 talent pools, 2 data rights requests — all with Irish names and realistic recruitment data.
