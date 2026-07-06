# ESTATE MAP — Ainm Product Estate Consolidation

Single source of truth for every capability across all codebases.
Produced 2026-07-05. Owner review required before any decommission action.

---

## Legend

| Tag | Meaning |
|-----|---------|
| **KEEP** | Stays where it is; no action required |
| **FOLD-INTO-AGENTIC-CORE** | Capability should be rebuilt on agentic-core primitives |
| **FOLD-INTO-EXECFLEX** | Capability migrates into ExecFlex as the unified product |
| **DUPLICATE** | Exists in multiple places; canonical winner declared below |
| **DEAD** | Unused, superseded, or abandoned; safe to archive |

---

## 1. CODEBASES

| Repo | Purpose | Stack | Branch | Status |
|------|---------|-------|--------|--------|
| **execflex-backend** | AI-voice-first recruitment platform API | Flask, Supabase PG, Python | rebuild-v1 | Active development |
| **execo-bridge** | ExecFlex frontend (agency console + public) | React 18, Vite, shadcn/ui, TS | rebuild-v1 | Active development |
| **agentic-core** | Shared agent library (primitives + domain agents) | Python, Anthropic SDK | recruitment-agents | Active development |
| **governance-platform** | EU AI Act compliance SaaS (standalone product) | FastAPI, SQLAlchemy, React | main | Live on compliance.ainm.ai |
| **transparency-platform** | EU Pay Transparency Directive compliance SaaS | FastAPI, SQLAlchemy, React, agentic-core v0.16.4 | master | Live on transparency.ainm.ai |
| **hr-advisory-agent** (ainm) | Cara HR advisor, BD agents, content engine | FastAPI, Supabase, React | main | Live on ainm.ai |

---

## 2. CAPABILITIES — AI / LLM

| Capability | Location | Evidence | Tag | Notes |
|-----------|----------|----------|-----|-------|
| LLM client (Anthropic raw SDK) | governance-platform `backend/app/services/ai_service.py:12` | `anthropic.AsyncAnthropic` direct | **DUPLICATE** | Winner: agentic-core `LLMClient` Protocol |
| LLM client (agentic-core Protocol) | agentic-core `src/agentic_core/llm/` | `LLMClient` Protocol, `AnthropicLLMClient` | **KEEP** | Canonical LLM abstraction |
| LLM client (ainm direct) | hr-advisory-agent `backend/app/agents/_llm_client.py` | Custom Anthropic wrapper | **DUPLICATE** | Winner: agentic-core `LLMClient` |
| LLM client (transparency-platform) | transparency-platform `backend/app/llm_client.py` | Via agentic-core v0.16.4 | **KEEP** | Already consuming agentic-core |
| SingleStepAgent pattern | agentic-core `src/agentic_core/agent.py` | `SingleStepAgent` class | **KEEP** | Canonical agent primitive |
| Model routing (TaskType→tier) | agentic-core `src/agentic_core/router.py` | `ModelRouter`, `TaskType` enum | **KEEP** | EXTRACTION→Haiku, REASONING→Sonnet |
| Prompt loader (YAML+Jinja2) | agentic-core `src/agentic_core/prompt.py` | `PromptLoader`, `StrictUndefined` | **KEEP** | Canonical prompt management |
| Match re-rank agent | agentic-core `agents/recruitment/match_rerank.py` | `MatchReRankAgent` | **KEEP** | REASONING tier, Sonnet |
| Screening summary agent | agentic-core `agents/recruitment/screening_summary.py` | `ScreeningSummaryAgent` | **KEEP** | REASONING tier, Sonnet |
| CV parser agent | agentic-core `agents/recruitment/cv_parser.py` | `CVParserAgent` | **KEEP** | EXTRACTION tier, Haiku |
| JD generator agent | agentic-core `agents/recruitment/jd_generator.py` | `JDGeneratorAgent` | **KEEP** | DRAFTING tier, Sonnet |
| Question flow (data) | agentic-core `agents/recruitment/question_flow.py` | 4 role flows, pure data | **KEEP** | No LLM |
| AI Act stage B summary | governance-platform `services/ai_service.py:30` | `generate_stage_b_summary()` | **FOLD-INTO-AGENTIC-CORE** | Port as ComplianceRiskAgent |
| AI Act stage B2 summary | governance-platform `services/ai_service.py:80` | `generate_stage_b2_summary()` | **FOLD-INTO-AGENTIC-CORE** | Port as ComplianceSectorAgent |
| AI Act stage C gaps | governance-platform `services/ai_service.py:117` | `generate_stage_c_summary()` | **FOLD-INTO-AGENTIC-CORE** | Port as ComplianceGapsAgent |
| AI Act stage D conformity | governance-platform `services/ai_service.py:153` | `generate_stage_d_summary()` | **FOLD-INTO-AGENTIC-CORE** | Port as ComplianceConformityAgent |
| AI Act scoring engine | governance-platform `services/ai_service.py:237` | `generate_scoring_report()` | **FOLD-INTO-AGENTIC-CORE** | Port as ComplianceScoringAgent |
| AI Act exec summary | governance-platform `services/ai_service.py:322` | `generate_executive_summary()` | **FOLD-INTO-AGENTIC-CORE** | Port as ComplianceExecSummaryAgent |
| Snapshot gap analysis | governance-platform `services/ai_service.py:373` | `generate_snapshot_gaps()` | **FOLD-INTO-AGENTIC-CORE** | Port as SnapshotGapAgent |
| Snapshot scorer (deterministic) | governance-platform `services/snapshot_scorer.py:37` | `calculate_snapshot_score()` | **FOLD-INTO-AGENTIC-CORE** | Pure logic, no LLM |
| Prohibited practices checker | governance-platform `services/prohibited_practices.py:4` | `check_prohibited_practices()` | **FOLD-INTO-AGENTIC-CORE** | Pure logic, Art. 5 rules |
| Compliance chat (streaming) | governance-platform `services/ai_service.py:430` | `stream_chat_response()` | **KEEP** | Stays in governance-platform until full fold |
| RAG service (ChromaDB) | governance-platform `services/rag_service.py` | `ingest_document()`, `query_chunks()` | **DUPLICATE** | No equivalent in agentic-core yet; keep in governance-platform for now |
| PDF compliance report | governance-platform `services/pdf_generator.py` | Full assessment PDF | **KEEP** | Governance-specific |

---

## 3. CAPABILITIES — RECRUITMENT / VOICE

| Capability | Location | Evidence | Tag | Notes |
|-----------|----------|----------|-----|-------|
| Voice screening (Cara/OpenAI Realtime) | execflex-backend `routes/cara_voice.py`, `routes/voice_websocket.py` | Twilio↔OpenAI bridge | **KEEP** | Core product |
| Voice monitor (uptime) | execflex-backend `routes/voice_monitor.py` | Health check loop | **KEEP** | Prod reliability |
| Matching engine (7-dimension) | execflex-backend `services/matching/engine.py` | `MatchingEngine` class | **KEEP** | 42 tests |
| Old match finder (token overlap) | execflex-backend `modules/match_finder.py` | Legacy `/match` endpoint | **DEAD** | Superseded by `services/matching/` |
| Screening state machine | execflex-backend `services/screening/state_machine.py` | CONSENT→INTAKE→SCORING | **KEEP** | 73 tests |
| Syndication engine | execflex-backend `services/syndication/engine.py` | LinkedIn/Indeed/IrishJobs/Google | **KEEP** | 52 tests |
| Compliance layer | execflex-backend `services/compliance/` | Decision log, data rights, human review | **KEEP** | 17 tests |
| Talent pool scaffold | execflex-backend `services/talent_pools/` | Assessment adapter (stub) | **KEEP** | Phase 2 |
| AI agent service | execflex-backend `services/ai/agent_service.py` | Flag-gated agent invocation | **KEEP** | Consumes agentic-core |
| Feature flag system | execflex-backend `services/ai/feature_flags.py` | Env-var based EXECFLEX_AI_* | **KEEP** | 21 tests |
| Billing (Stripe) | execflex-backend `routes/billing.py` | ExecFlex billing | **KEEP** | |
| Auto match service | execflex-backend `services/auto_match_service.py` | Automated matching | **KEEP** | |
| Apollo sourcing | execflex-backend `services/apollo_service.py` | Candidate sourcing | **KEEP** | |
| LinkedIn service | execflex-backend `services/linkedin_service.py` | LinkedIn integration | **KEEP** | |

---

## 4. CAPABILITIES — GOVERNANCE / COMPLIANCE

| Capability | Location | Evidence | Tag | Notes |
|-----------|----------|----------|-----|-------|
| 5-stage assessment flow (A→B→B2→C→D) | governance-platform `routers/assessments.py` | Full CRUD + stage submission | **KEEP** (+ fold engine) | Router stays; AI engine ports |
| AI system inventory | governance-platform `models/ai_system.py` | CRUD, versioning, lifecycle | **KEEP** | |
| Organisation + billing | governance-platform `models/organisation.py`, `routers/billing.py` | Stripe subscription tiers | **KEEP** | |
| Review reminders (6-month) | governance-platform `models/review_reminder.py`, `tasks/reminder_checker.py` | Auto-schedule post-assessment | **KEEP** | |
| Compliance calendar | governance-platform `frontend/src/pages/ComplianceCalendar.tsx` | Review schedule UI | **KEEP** | |
| Assessment wizard UI | governance-platform `frontend/src/pages/AssessmentWizard.tsx` | Multi-stage form | **KEEP** | |
| Snapshot (public lead gen) | governance-platform `routers/snapshot.py` | Unauthenticated scoring | **KEEP** | SECURITY: no rate limiting |

---

## 5. CAPABILITIES — PAY TRANSPARENCY

| Capability | Location | Evidence | Tag | Notes |
|-----------|----------|----------|-----|-------|
| Pay equity analysis | transparency-platform `backend/app/` | Job evaluation, gender gap, bias | **KEEP** | 183 tests |
| DFY pack pipeline | transparency-platform, via agentic-core | `build_dfy_pack_pipeline` | **KEEP** | agentic-core v0.16.4 |
| Consultant review | transparency-platform `routers/` | Review queue, release flow | **KEEP** | |
| Data provider | transparency-platform `services/data_provider.py` | `TransparencyPlatformDataProvider` | **KEEP** | Implements agentic-core Protocol |

---

## 6. CAPABILITIES — AINM (HR-ADVISORY-AGENT)

| Capability | Location | Evidence | Tag | Notes |
|-----------|----------|----------|-----|-------|
| Cara voice advisor | hr-advisory-agent `backend/app/ai/hr_agent.py` | HR Q&A chatbot | **KEEP** | Core product |
| BD outreach agents | hr-advisory-agent `backend/app/agents/bd_outreach.py` + _bd_* | Email discovery, drafting | **KEEP** | Revenue critical |
| Content engine | hr-advisory-agent `backend/app/agents/content_engine.py` | Blog/social generation | **KEEP** | |
| Cold email pipeline | hr-advisory-agent `backend/app/agents/cold_send.py` | IMAP, suppress, sign | **KEEP** | |
| TShock orchestrator | hr-advisory-agent `backend/app/agents/tshock/` | Multi-tool agent | **KEEP** | |
| Compliance monitor | hr-advisory-agent `backend/app/agents/compliance_monitor.py` | Regulation tracking | **KEEP** | |
| Uptime monitor | hr-advisory-agent `backend/app/agents/uptime_monitor.py` | Service health | **KEEP** | |
| Admin router | hr-advisory-agent `backend/app/admin/router.py` | Company admin panel | **KEEP** | Transcript visibility concern |

---

## 7. CAPABILITIES — FRONTEND (EXECO-BRIDGE)

| Capability | Location | Evidence | Tag | Notes |
|-----------|----------|----------|-----|-------|
| Agency dashboard | execo-bridge `src/pages/agency/AgencyDashboard.tsx` | Stat cards, quick actions | **KEEP** | |
| Job form (+ AI JD gen) | execo-bridge `src/pages/agency/JobForm.tsx` | Pay transparency enforced | **KEEP** | |
| Jobs list | execo-bridge `src/pages/agency/JobsList.tsx` | | **KEEP** | |
| Pipeline board | execo-bridge `src/pages/agency/PipelineBoard.tsx` | Kanban drag | **KEEP** | |
| Candidate profile (+ AI cards) | execo-bridge `src/pages/agency/CandidateProfile.tsx` | Screening + match AI | **KEEP** | |
| Screening review | execo-bridge `src/pages/agency/ScreeningReview.tsx` | Human oversight queue | **KEEP** | |
| Compliance centre | execo-bridge `src/pages/agency/ComplianceCentre.tsx` | Decisions + data rights | **KEEP** | |
| Talent pools | execo-bridge `src/pages/agency/TalentPools.tsx` | | **KEEP** | |
| Executive onboarding (legacy) | execo-bridge `src/pages/ExecutiveOnboarding.tsx` + 11 steps | Multi-step form | **DEAD** | Legacy pre-rebuild flow |
| Executive matching (legacy) | execo-bridge `src/components/executive-matching/` | Filter sidebar, match cards | **DEAD** | Replaced by v1 matching engine |
| Post role (legacy) | execo-bridge `src/components/post-role/` | Multi-step form | **DEAD** | Replaced by v1 JobForm |
| Old API client | execo-bridge `src/lib/api.ts` | Pre-v1 API calls | **DEAD** | Replaced by `api-v1.ts` |

---

## 8. DUPLICATE ANALYSIS — CANONICAL WINNERS

| Duplicate | Locations | Canonical Winner | Migration Note |
|-----------|-----------|-----------------|----------------|
| **LLM Client** | governance-platform (raw Anthropic SDK), hr-advisory-agent (`_llm_client.py`), agentic-core (`LLMClient` Protocol) | **agentic-core `LLMClient`** | governance-platform AI service should consume agentic-core agents, not raw SDK. ainm should migrate `_llm_client.py` to agentic-core client. |
| **Model hardcoding** | governance-platform hardcodes `claude-opus-4-6` in 4 places, `claude-sonnet-4-6` in 2 | **agentic-core `ModelRouter`** | Ported agents use TaskType routing. No hardcoded model strings. |
| **Compliance scoring** | governance-platform `snapshot_scorer.py` (deterministic), governance-platform `ai_service.py` (LLM) | **agentic-core compliance module** | Both port into agentic-core. Deterministic scorer as pure function, LLM scorer as SingleStepAgent. |
| **PDF generation** | governance-platform `services/pdf_generator.py` | **governance-platform** (sole user) | No duplicate — keep in place. ExecFlex doesn't need compliance PDFs yet. |
| **Auth pattern** | governance-platform (FastAPI Depends), execflex-backend (Flask decorator), transparency-platform (FastAPI Depends), ainm (FastAPI Depends) | **No canonical winner** | Different frameworks. Not a consolidation target. |
| **Billing/Stripe** | governance-platform `routers/billing.py`, execflex-backend `routes/billing.py`, transparency-platform `routers/billing.py`, ainm `routers/billing.py` | **Per-product** | Each product has its own Stripe config. Cannot consolidate — different products, different pricing. |
| **Email service** | governance-platform `services/email_service.py`, execflex-backend `modules/email_sender.py`, ainm (Resend) | **Per-product** | Different providers, different templates. Low consolidation value. |

---

## 9. DECOMMISSION CHECKLIST — GOVERNANCE-PLATFORM

**Status: PREPARE ONLY. Owner decision required before any action.**

When the compliance engine is fully ported to agentic-core and the ExecFlex frontend surfaces the assessment flow, governance-platform can be archived. Before that:

### Pre-decommission

- [ ] **Back up database**: `pg_dump` the governance-platform PostgreSQL database. Contains: organisations, users, ai_systems, assessments, documents, invitations, review_reminders, snapshot_leads.
- [ ] **Back up uploaded documents**: `/app/chroma_data/` (ChromaDB vector store) + any file uploads in the deployment volume.
- [ ] **Export Stripe state**: List all active subscriptions (`stripe subscriptions list --status=active`). Cancel or migrate before shutdown.
- [ ] **Check active users**: Query `SELECT COUNT(*) FROM users WHERE last_login > NOW() - INTERVAL '90 days'`. If >0, notify before decommission.
- [ ] **DNS**: compliance.ainm.ai — redirect to ExecFlex compliance page or sunset notice.
- [ ] **API consumers**: Check if any external system calls governance-platform APIs (snapshot endpoint is public — may have embeds).

### Endpoints to close

- `POST /snapshot/score` — public, unauthenticated. Move to ExecFlex or remove.
- `POST /chat/{system_id}` — streaming chat. Replaced by ExecFlex compliance chat if built.
- All `/assessments/*` routes — replaced by ExecFlex compliance module.
- All `/ai-systems/*` routes — replaced by ExecFlex AI system inventory.
- `/billing/*` — Stripe webhooks. Must update Stripe dashboard webhook URL.

### What to archive (not delete)

- Full git repo (already on GitHub)
- Database dump
- ChromaDB data
- Stripe subscription records
- Any uploaded compliance documents

---

## 10. KEY FINDINGS

1. **governance-platform's AI service hardcodes `claude-opus-4-6`** in 4 stage-summary functions and `claude-sonnet-4-6` in 2 (scoring + snapshot gaps). The agentic-core port must use TaskType routing, not hardcoded models.

2. **Three separate LLM client implementations** exist across the estate. Only transparency-platform properly uses agentic-core's client. governance-platform and ainm both use raw Anthropic SDK calls.

3. **governance-platform's snapshot endpoint is public and unauthenticated** (`POST /snapshot/score`). It accepts arbitrary input and calls Claude for gap analysis. No rate limiting. Attack surface: prompt injection via crafted answers, cost amplification via repeated calls.

4. **Legacy code in execo-bridge**: ~25 legacy pages/components from the pre-rebuild era (executive onboarding, old matching, post-role flow) are dead code. They reference `api.ts` (the old client). Safe to prune after confirming no routes point to them.

5. **transparency-platform pins agentic-core v0.16.4** via git tag. ExecFlex pins the `recruitment-agents` branch. Production requires a v0.17.0 release that passes both consumers' suites (transparency 183 + execflex 217 + new compliance tests).

6. **ainm (hr-advisory-agent) has its own LLM client** (`_llm_client.py`) that duplicates agentic-core functionality. Migration path: import `AnthropicLLMClient` from agentic-core instead.
