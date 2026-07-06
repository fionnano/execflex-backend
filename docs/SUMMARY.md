# SUMMARY — ExecFlex v1 Rebuild + AI-First Integration + Estate Consolidation

Decisions the owner most needs to review, ordered by how wrong I might be. Fold-boundary calls (shared-library, compliance module, privacy) at the top.

---

## C-1. Compliance module replaces governance-platform's 7 functions with 5 agents (D-34) — HIGH UNCERTAINTY

Ported governance-platform's AI Act assessment engine into agentic-core as `agents/compliance/`. Replaced 7 interwoven service functions with 5 clean agents: 2 pure logic (prohibited practices, snapshot scorer) + 3 LLM-powered (risk summary, scoring engine, snapshot gaps). The rewrite changes the scoring algorithm — governance-platform's `_calculate_risk_score` uses different weights than the new `calculate_snapshot_score`. Any org comparing old vs new scores will see different numbers.

**Risk:** Score drift between governance-platform and the new compliance module. Existing governance-platform users may question why their score changed. Should document the delta before decommissioning governance-platform.

## C-2. Compliance module multi-consumer gate — not production-consumable yet (D-35) — HIGH UNCERTAINTY

Production consumption of the compliance module requires: transparency-platform suite passes (231 tests), ExecFlex suite passes (217 tests), AND new compliance tests pass (131 tests). Tonight's work is on `recruitment-agents` branch only. A premature merge could break transparency-platform's consumption of agentic-core.

**Risk:** Branch divergence. The longer `recruitment-agents` stays unmerged, the harder the merge becomes.

## C-3. Cara transcript privacy defaults to OFF — GDPR-safe but limits admin visibility (D-36) — MEDIUM-HIGH UNCERTAINTY

Admin transcript visibility for Cara (ainm/hr-advisory-agent) defaults to False. Admins see aggregate-only views (message count, topic summary) for other employees' conversations. This is GDPR-safe but means an employer investigating a legitimate HR concern (e.g. misconduct investigation with legal basis) cannot access transcripts without first toggling the setting.

**Risk:** Employers may expect access by default. The toggle is per-company, not per-conversation — it's all-or-nothing. A more nuanced approach (per-conversation access with legal basis documentation) would be safer but was cut for scope.

## C-4. governance-platform RAG capability has no agentic-core equivalent (D-37) — MEDIUM UNCERTAINTY

governance-platform has a ChromaDB-backed RAG service used for Stage D assessment completion. No equivalent exists in agentic-core. If governance-platform is decommissioned before a RAG primitive is built, document intelligence is lost.

**Risk:** Decommissioning governance-platform prematurely. RAG primitive is a prerequisite.

## C-5. Frontend AI badges assume all unreviewed decisions are AI-generated (D-38) — LOW-MEDIUM UNCERTAINTY

The consistency pass added Sparkles + "AI" badges to all pending decision cards on AgencyDashboard, ScreeningReview, and ComplianceCentre. In practice, ALL decisions in the log ARE AI-generated (the compliance layer only logs AI-initiated decisions). But if manual decisions are ever logged, they'll also show the AI badge.

**Risk:** Misleading if non-AI decisions enter the log. The fix is to check `ai_generated` field on each decision, but the field isn't surfaced in the current API response.

---

## 0a. agentic-core consumed via branch pin, not tagged release (D-27) — HIGH UNCERTAINTY

ExecFlex pins agentic-core's `recruitment-agents` branch. Production requires a v0.17.0 release through the two-consumer gate (transparency-platform must also pass its suite). Tonight's branch pin is inherently fragile — a force-push to the branch breaks ExecFlex's install.

**Risk:** Branch reference in requirements.txt is not reproducible. Must tag before any deploy.

## 0b. Recruitment agents live in agentic-core, not ExecFlex (Architecture Decision) — MEDIUM-HIGH UNCERTAINTY

Five recruitment agents (match re-rank, screening summary, CV parser, JD generator, question flow) were built INTO agentic-core as `agentic_core.agents.recruitment.*`, not in ExecFlex. This follows the owner's architecture decision: agentic-core is the shared library, ExecFlex is a consumer. But: these agents have zero consumers besides ExecFlex today. If they turn out to be ExecFlex-specific, they're in the wrong repo.

**Risk:** Coupling ExecFlex-specific logic to the shared library. If a second consumer (hr-advisory-agent) doesn't need them, they should move.

## 0c. Feature flags are env-var based, not per-org (D-26) — MEDIUM UNCERTAINTY

All orgs see the same AI feature state. A fintech agency that wants AI re-ranking gets it at the same time as a tiny agency that doesn't. Per-org flags need a settings table and admin UI — both cut.

**Risk:** Can't gradually roll out. First agency that complains about AI outputs has no per-org kill switch.

## 0d. LLM agents fail gracefully but silently (D-33) — MEDIUM UNCERTAINTY

When ANTHROPIC_API_KEY is missing or the LLM errors, the agent service returns None and the endpoint serves deterministic-only results. No user-visible error, no indicator that AI was supposed to run but didn't. The ops team sees ERROR logs but the recruiter sees nothing.

**Risk:** "AI is enabled but doesn't seem to be doing anything" support tickets. Should add a status indicator showing whether the LLM actually ran.

## 0e. Heuristic + LLM dual scoring, not LLM replacement (D-29) — MEDIUM UNCERTAINTY

The ScreeningSummaryAgent supplements the heuristic scorer, doesn't replace it. Both run. The human reviewer sees two assessments: a number (heuristic) and a narrative (LLM). If they disagree, the reviewer must reconcile. This is intentionally conservative but may confuse users.

**Risk:** Two conflicting signals. The heuristic says "proceed" (high numeric score) but the LLM summary says "decline" (identified critical gaps). Which does the reviewer trust?

## 1. Heuristic scoring instead of LLM scoring (D-06) — HIGH UNCERTAINTY

The screening state machine scores answers by response length (1-5 scale). "AI scoring" is barely AI — it's string-length heuristics. A verbose poor answer scores higher than a concise excellent one. The LLM scoring replacement is critical for production use, and Art. 10 data governance documentation needs updating when it ships.

**Risk:** Screening recommendations are arbitrary. First real user will notice immediately.

## 2. Pipeline stages as PostgreSQL enum, not per-org configurable (D-24) — HIGH UNCERTAINTY

Stages are fixed: sourced → screened → shortlisted → interviewing → offered → placed → rejected → withdrawn. Agencies that use different terminology ("submitted", "presented", "qualified") cannot customise. Changing later requires a PostgreSQL migration to alter the enum type.

**Risk:** First agency customer says "we don't use those stage names" and you're stuck.

## 3. IrishJobs adapter uses best-guess XML format (D-15) — HIGH UNCERTAINTY

IrishJobs doesn't publish a public XML feed spec. The adapter produces a generic XML format. If IrishJobs has a private partner API (likely), this adapter won't work at all.

**Risk:** Zero confidence this format is accepted. May need complete rewrite.

## 4. org_id from JWT assumes Supabase metadata structure (D-16) — MEDIUM-HIGH UNCERTAINTY

`extract_org_context()` reads `user_metadata.organization_id` from the JWT. This assumes the Supabase project stores orgs in user metadata, and that multi-org users pick one org at login. Neither assumption is validated against the actual Supabase config.

**Risk:** If metadata structure differs, every authenticated endpoint breaks.

## 5. Human review gate validates on reason length >= 3 chars (D-17) — MEDIUM UNCERTAINTY

`require_human_review_for_reject()` considers any reason >= 3 characters valid. "N/A" passes. The EU AI Act requires "meaningful" human oversight, not just character counting. An auditor may challenge whether a 3-char reason satisfies Art. 14.

**Risk:** Compliance theatre. Should probably require structured reason (dropdown + free text).

## 6. Pay range enforced at API layer but not database layer (D-18) — MEDIUM UNCERTAINTY

`pay_range_min` and `pay_range_max` are required in the API route but the DB columns are nullable. Direct Supabase inserts could create jobs without pay ranges, violating the Pay Transparency Directive.

**Risk:** Add NOT NULL or CHECK constraints to the database columns.

## 7. Skills matching is set overlap, not semantic (D-02) — MEDIUM UNCERTAINTY

`_score_skills` does exact set intersection. "Python" matches but "Python development" doesn't. "ML" doesn't match "machine learning". No synonym expansion or embedding similarity.

**Risk:** False negatives on nearly every search with non-identical terminology.

## 8. Data rights endpoint has no rate limiting (D-21) — LOW-MEDIUM UNCERTAINTY

`POST /api/v1/compliance/data-rights` is public (no auth) so candidates can submit GDPR requests. No rate limiting, CAPTCHA, or abuse prevention. Could be spammed.

**Risk:** Spam, not security. Add rate limiting before going live.

## 9. Assessment adapter always returns 85 / passed (D-22) — LOW-MEDIUM UNCERTAINTY

`StubAssessmentAdapter` is the only adapter. "ExecFlex Verified" badges would show every candidate as verified with 85%. Fine for dev, dangerous if stub data reaches external stakeholders.

**Risk:** Demo data contamination. Distinguish stub results visually.

## 10. Syndication adapters don't track feed regeneration timing (D-15) — LOW UNCERTAINTY

When a job is updated or closed, the syndication table records original submission but doesn't trigger feed regeneration. Job boards polling the XML feed get stale data.

**Risk:** Stale postings on boards after closing a job. Need webhook or event hook on job status changes.

---

## C-6. ISO 27001 SoA maps 93 controls but implementation is self-assessed — MEDIUM UNCERTAINTY

Self-assessed 93 Annex A controls: 15 fully implemented, 40 partial, 15 not implemented, 23 not applicable. The "implemented" assessments are based on code evidence (tests, auth middleware, HTTPS), not an external audit. An auditor may downgrade several "implemented" ratings.

**Risk:** Overconfident status claims. Several "IMPLEMENTED" controls (A.5.15 access control, A.8.28 secure coding) are strong but have no third-party verification.

## C-7. 28-item gap list prioritises credential rotation above all else — LOW-MEDIUM UNCERTAINTY

GAP_LIST.md ranks G-001 (rotate exposed credentials) as the #1 blocker. This aligns with R-001 (score 15, highest in risk register). But the recommended "this week" timeline is aggressive — git history scrubbing requires coordination and may break CI/CD if done carelessly.

**Risk:** Credential rotation is critical but complex. A rushed rotation could lock out running services.

---

## Deliverables Completed

### Phase 1 — v1 Rebuild

| Item | Location | Status |
|------|----------|--------|
| Data model migration | `supabase/migrations/20260704_rebuild_v1_schema.sql` | Done |
| Multi-tenant v1 API | `routes/api_v1/` (10 route files) | Done |
| Auth layer | `services/api/` | Done |
| Matching engine | `services/matching/` | Done — 42 tests |
| Screening state machine | `services/screening/` | Done — 73 tests |
| Syndication engine | `services/syndication/` | Done — 52 tests |
| Compliance layer | `services/compliance/` | Done — 17 tests |
| Security verification | `test/test_security_verification.py` | Done — 12 tests |
| Talent pool scaffold | `services/talent_pools/` | Done |
| AI Act compliance doc | `docs/AI_ACT_COMPLIANCE.md` | Done |
| Verification methodology | `docs/VERIFICATION_METHODOLOGY.md` | Done |
| Decisions log | `docs/DECISIONS.md` | Done (D-01 to D-33) |
| Demo script | `docs/DEMO_SCRIPT.md` | Done |

### Phase 2 — AI-First Integration (agentic-core)

| Item | Location | Status |
|------|----------|--------|
| Match re-rank agent (REASONING) | `agentic-core: agents/recruitment/match_rerank.py` | Done |
| Screening summary agent (REASONING) | `agentic-core: agents/recruitment/screening_summary.py` | Done |
| CV parser agent (EXTRACTION/Haiku) | `agentic-core: agents/recruitment/cv_parser.py` | Done |
| JD generator agent (DRAFTING/Sonnet) | `agentic-core: agents/recruitment/jd_generator.py` | Done |
| Question flow data module | `agentic-core: agents/recruitment/question_flow.py` | Done |
| Prompt templates (4) | `agentic-core: agents/recruitment/prompts/*.md` | Done |
| Agent test suite (108 new) | `agentic-core: tests/test_*.py` | Done — 605 total |

### Phase 2 — AI-First Integration (ExecFlex consumer)

| Item | Location | Status |
|------|----------|--------|
| Feature flag system | `services/ai/feature_flags.py` | Done |
| Agent service layer | `services/ai/agent_service.py` | Done |
| AI API endpoints | `routes/api_v1/ai.py` | Done |
| Match re-rank wiring | `routes/api_v1/matches.py` | Done |
| Screening summary wiring | `routes/api_v1/screens.py` | Done |
| AI feature flag tests | `test/test_ai_feature_flags.py` | Done — 21 tests |

### Phase 2 — Frontend AI Surfacing (execo-bridge)

| Item | Location | Status |
|------|----------|--------|
| AI types + API client | `execo-bridge: src/lib/api-v1.ts` | Done |
| AI screening summary card | `execo-bridge: src/pages/agency/CandidateProfile.tsx` | Done |
| AI match rationale card | `execo-bridge: src/pages/agency/CandidateProfile.tsx` | Done |
| JD generator button + UI | `execo-bridge: src/pages/agency/JobForm.tsx` | Done |
| Build verification | `vite build` passes | Done |

### Phase 3 — Estate Consolidation: Compliance Module (agentic-core)

| Item | Location | Status |
|------|----------|--------|
| Prohibited practices checker (pure logic) | `agentic-core: agents/compliance/prohibited_practices.py` | Done |
| Snapshot scorer (pure logic) | `agentic-core: agents/compliance/snapshot_scorer.py` | Done |
| Risk summary agent (LLM/SYNTHESIS) | `agentic-core: agents/compliance/risk_summary.py` | Done |
| Scoring engine agent (LLM/REASONING) | `agentic-core: agents/compliance/scoring_engine.py` | Done |
| Snapshot gaps agent (LLM/REASONING) | `agentic-core: agents/compliance/snapshot_gaps.py` | Done |
| Compliance test suite (131 new) | `agentic-core: tests/test_compliance_*.py` | Done |

### Phase 3 — Estate Consolidation: ExecFlex Compliance Wiring

| Item | Location | Status |
|------|----------|--------|
| Compliance feature flag | `services/ai/feature_flags.py` | Done |
| Compliance agent service | `services/ai/agent_service.py` | Done |
| Compliance API endpoints | `routes/api_v1/ai.py` | Done |

### Phase 3 — Frontend Consistency Pass (execo-bridge)

| Item | Location | Status |
|------|----------|--------|
| Error states on 4 pages | PipelineBoard, ScreeningReview, ComplianceCentre, AgencyDashboard | Done |
| AI badges on pending decisions | AgencyDashboard, ScreeningReview, ComplianceCentre | Done |
| AI Act Snapshot tab | ComplianceCentre (5-question form + score display + gaps) | Done |
| Compliance API types | `src/lib/api-v1.ts` | Done |
| Build verification | `vite build` + `tsc --noEmit` pass | Done |

### Phase 4 — Cara Transcript Privacy (hr-advisory-agent)

| Item | Location | Status |
|------|----------|--------|
| Aggregate-only schema | `backend/app/modules/faq/schemas.py` | Done |
| Privacy helper + endpoint guards | `backend/app/modules/faq/router.py` | Done |
| Privacy settings GET/PUT endpoints | `backend/app/modules/faq/router.py` | Done |

### Phase 4 — Production Hygiene Audit

| Item | Location | Status |
|------|----------|--------|
| ESTATE_MAP.md | `execflex-backend/ESTATE_MAP.md` | Done |
| PROD_CLEANUP.md | `execflex-backend/PROD_CLEANUP.md` | Done |

### Session 2 — Security Hardening (Phases 1-2)

| Item | Location | Status |
|------|----------|--------|
| Snapshot rate limiter | `governance-platform: backend/app/core/rate_limiter.py` | Done |
| Snapshot input validation | `governance-platform: backend/app/routers/snapshot.py` | Done |
| Auth rate limiting | `governance-platform: backend/app/routers/auth.py` | Done |
| Smoke test production guard | `execflex-backend: utils/auth_helpers.py` | Done |
| SECURITY_CLOSURE.md | `execflex-backend/SECURITY_CLOSURE.md` | Done |

### Session 2 — Governance-Platform Overhaul

| Item | Location | Status |
|------|----------|--------|
| Model routing (env-var configurable) | `governance-platform: backend/app/services/ai_service.py` | Done |
| Structured logging + PII sanitizer | `governance-platform: backend/app/core/logging_config.py` | Done |
| Shared `_complete()` LLM wrapper | `governance-platform: backend/app/services/ai_service.py` | Done |
| Test suite (42 tests) | `governance-platform: backend/tests/` | Done |

### Session 2 — ISO 27001 + 42001 Scaffold

| Item | Location | Status |
|------|----------|--------|
| Statement of Applicability (93 controls) | `execflex-backend/iso/STATEMENT_OF_APPLICABILITY.md` | Done |
| Asset Register | `execflex-backend/iso/ASSET_REGISTER.md` | Done |
| Risk Register (20 risks, L×I scoring) | `execflex-backend/iso/RISK_REGISTER.md` | Done |
| Incident Response Policy | `execflex-backend/iso/INCIDENT_RESPONSE.md` | Done |
| Change Management Policy | `execflex-backend/iso/CHANGE_MANAGEMENT.md` | Done |
| AI Management System (ISO 42001) | `execflex-backend/iso/AI_MANAGEMENT_SYSTEM_42001.md` | Done |
| Gap List (28 gaps, prioritised) | `execflex-backend/iso/GAP_LIST.md` | Done |

### Session 2 — Defect Fixes

| Item | Location | Status |
|------|----------|--------|
| List numbering in DFY pack PDFs | `transparency-platform: backend/app/services/dfy_pack_pdf.py` | Done |

## Test Summary

| Suite | Tests | Time |
|-------|-------|------|
| Matching engine | 42 | <0.1s |
| Screening state machine | 73 | <0.1s |
| Syndication | 52 | <0.1s |
| Compliance | 17 | <0.1s |
| Security verification | 12 | <0.1s |
| AI feature flags | 21 | <0.1s |
| **ExecFlex total** | **217** | **<0.3s** |

| Suite (agentic-core) | Tests | Time |
|----------------------|-------|------|
| Match re-rank agent | ~25 | <0.1s |
| Screening summary agent | ~20 | <0.1s |
| CV parser agent | ~20 | <0.1s |
| JD generator agent | ~20 | <0.1s |
| Question flow data | ~23 | <0.1s |
| Prohibited practices | 18 | <0.1s |
| Snapshot scorer | 28 | <0.1s |
| Risk summary agent | ~20 | <0.1s |
| Scoring engine agent | ~30 | <0.1s |
| Snapshot gaps agent | ~20 | <0.1s |
| **New recruitment tests** | **~108** | **<0.2s** |
| **New compliance tests** | **~131** | **<0.3s** |
| **agentic-core total** | **736** | **<1s** |

| Suite (governance-platform) | Tests | Time |
|-----------------------------|-------|------|
| Snapshot scorer | 17 | <0.1s |
| Prohibited practices | 13 | <0.1s |
| Rate limiter | 5 | <0.1s |
| Logging sanitizer | 6 | <0.1s |
| **governance-platform total** | **42** | **<0.1s** |

## Branch Map

| Repo | Branch | Head | Status |
|------|--------|------|--------|
| execflex-backend | rebuild-v1 | d7c66be | ISO scaffold committed |
| execflex-backend | security-hardening | b7a077a | Smoke test guard + SECURITY_CLOSURE.md |
| agentic-core | recruitment-agents | 525c8c4 | Compliance module committed |
| execo-bridge | rebuild-v1 | 06e46ed | Consistency pass committed |
| hr-advisory-agent | cara-privacy | 117b73c | Privacy toggle committed |
| governance-platform | security-hardening | 7bf6d5d | Rate limiting + input validation |
| governance-platform | overhaul-2026-07 | 98951f5 | Model routing + tests + logging |
| transparency-platform | defect-fixes | 116769e | DFY pack list-numbering fix |
