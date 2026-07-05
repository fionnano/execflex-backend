# Decisions Log — ExecFlex v1 Rebuild + Estate Consolidation

Decisions made during autonomous rebuild and consolidation. Numbered for reference.

## D-01: New matching engine lives at `services/matching/`, not as a replacement for `modules/match_finder.py`

Existing `/match` endpoint uses `match_finder.py` with token-overlap scoring. Rather than rip it out (risking the live voice pipeline), the new multi-signal matching engine sits in its own module. Old endpoint stays alive on the old code; new API will use `services/matching/`.

## D-02: 7-dimension weighted scoring model (skills_fit=0.25 is the heaviest)

Weights: skills_fit=0.25, industry_fit=0.20, experience_fit=0.15, location_fit=0.10, availability_fit=0.10, compensation_fit=0.10, screening_fit=0.10. Skills weighed highest because recruiter feedback consistently says "can they do the job" is the #1 filter. Weights auto-normalize if overridden.

## D-03: LLM re-rank is a Protocol interface only — not implemented

`Reranker` is a structural typing Protocol with a `rerank()` method. Zero LLM calls in the v1 engine. This is deliberate: the deterministic scorer must work without API keys, and LLM re-rank is a Phase 2 item per TARGET_ARCHITECTURE.md.

## D-04: Screening state machine states map to existing CallPhase enum

IDLE=CONNECTING, CONSENT=GREETING, INTAKE=DISCOVERY, SCORING/COMPLETE=CLOSING/ENDED. The new ScreeningState enum is separate (not a subclass) because the semantics differ, but the mapping is documented so the voice pipeline can bridge them later.

## D-05: Consent-first flow with GDPR disclosure is mandatory for candidate sessions

Every candidate session starts in CONSENT state with a GDPR/recording disclosure. Cannot proceed to INTAKE without explicit consent. Client sessions get a lighter disclosure. This is a legal requirement, not optional.

## D-06: Heuristic scoring (length-based 1-5) is a placeholder for LLM scoring

`_heuristic_score()` maps response length to a 1-5 score. This is intentionally crude — the point is to have the state machine produce numeric outcomes that the matching engine can consume via `screening_recommendation`. LLM-based scoring replaces this in Phase 2.

## D-07: Handoff triggers for distress phrases (lawyer, discrimination, harassment)

If a candidate says any distress phrase, the state machine transitions to HANDOFF immediately. This is a safety mechanism — the system should never continue automated screening when a candidate signals distress or legal escalation.

## D-08: Voice monitor disabled via config flag, not removed

Added `VOICE_MONITOR_ENABLED` env var (default: true). The monitor thread only starts if the flag is true. This preserves the code for production use while allowing it to be disabled during rebuild work. Set `VOICE_MONITOR_ENABLED=false` in `.env` to disable.

## D-09: Did NOT fix the 3 critical security findings (S-001, S-002, S-003) as patches

Per hard constraint: "Do not fix the 3 critical security findings as patches on the old code — they die in the target architecture." TARGET_ARCHITECTURE.md documents the replacement designs (org_id enforcement, API key rotation, credential vault).

## D-10: Synthetic data only — zero real candidate/client data in any test or fixture

All 50 candidates and 20 roles in the test suite are invented. No queries against the production database. No real names, emails, or identifiable information used anywhere.

## D-11: Client intake produces a StructuredBrief, candidate intake produces a ScreeningOutcome

Different session types produce different artifacts. This reflects the product reality: agencies need a structured role brief from client intake, and a scored recommendation from candidate intake. Both feed into the matching engine differently.

## D-12: NED penalty is multiplicative (x0.3), not additive

When a role requires NED (Non-Executive Director) experience and the candidate doesn't have it, the composite score is multiplied by 0.3 rather than subtracting a fixed amount. This ensures NED roles strongly prefer NED candidates regardless of other dimensions.

## D-13: Passive candidates get a 0.85 multiplier, closed candidates get 0.1

Passive candidates (open_to="passive") are still valid matches but slightly deprioritized. Candidates explicitly closed to opportunities (open_to="no") are nearly eliminated from results but not completely zeroed out, in case an agency wants to see them.

## D-14: Compensation scoring uses candidate minimum ask vs role budget max

The scorer checks if the candidate's minimum compensation expectation fits within the role's budget range. If there's overlap, it scores high. This reflects recruiter reality — the key question is "can we afford them" rather than exact bracket matching.

---

## Phase 1 Backend — rebuild-v1 decisions

## D-15: IrishJobs adapter uses generic XML format (no public feed spec)

IrishJobs doesn't publish a public XML feed specification. The adapter produces a generic `<jobs source="ExecFlex">` format with `<region>` extracted from location. If IrishJobs ever publishes a spec, swap the adapter implementation — the BoardAdapter protocol makes this a one-file change.

## D-16: org_id from JWT only — never from request body

All v1 API endpoints extract organization_id from JWT claims via `extract_org_context()`. No endpoint accepts org_id as a request parameter. This eliminates the entire class of org isolation bugs (S-003) by design. Verified by `test_security_verification.py::TestOrgIsolation`.

## D-17: Human review gate blocks ALL automated terminal decisions, not just AI-initiated ones

`require_human_review_for_reject()` blocks any reject/withdraw action that lacks an authenticated human user and a reason. This is stricter than what GDPR Art. 22 requires (which only covers "solely automated" decisions), but erring on the safe side for a high-risk system. The gate checks: (a) context exists with user_id, (b) reason provided and >= 3 chars.

## D-18: Pay range required on every job posting — API rejects without it

`POST /api/v1/jobs` returns 400 if `pay_range_min` or `pay_range_max` is missing. This implements the EU Pay Transparency Directive (2023/970) at the API layer so it's impossible to create a non-compliant posting. Adapters then include pay data in all syndication feeds.

## D-19: Screening session state persisted as JSON, reconstructed on each API call

`screening_sessions` table stores `questions`, `answers`, `transitions`, `current_state` as JSONB. Each API call (`/consent`, `/answer`, `/score`) restores the `ScreeningStateMachine` from stored JSON, processes the action, and saves back. This keeps the API stateless while preserving the full state machine semantics. Trade-off: JSON reconstruction is slower than in-memory, but correctness and crash-resilience outweigh the ~1ms overhead.

## D-20: AI decision log records inputs and model — not just outcomes

`ai_decision_log` stores `inputs_summary` (JSONB), `model_version` (TEXT), `score`, `explanation`, and `dimension_scores_json`. This satisfies EU AI Act Art. 12 record-keeping: the full decision context is auditable, not just the result. The `model_version` field will be "heuristic-v1" until LLM scoring ships.

## D-21: Data rights requests are public-facing — no auth required to submit

`POST /api/v1/compliance/data-rights` accepts requests without authentication so candidates who don't have accounts can exercise GDPR Art. 15/17 rights. The endpoint requires name, email, and request type. Processing is org-scoped and requires owner role.

## D-22: Assessment adapter is Protocol-based, stub only in v1

`AssessmentAdapter` is a structural typing Protocol like `Reranker`. The `StubAssessmentAdapter` always returns score 85.0 / passed=True. Real provider integrations (Codility, SHL) are Phase 2. The talent pool data model is in the migration but the "verified" workflow is scaffolded, not wired.

## D-23: Google Indexing adapter produces JSON stubs, no real API calls

The `GoogleIndexingStubAdapter` generates JSON payloads matching the Google Indexing API format but doesn't call the API. Real implementation needs OAuth2 service account credentials and is a Phase 2 integration. The stub lets us test the syndication pipeline end-to-end without credentials.

## D-24: Pipeline stages are enum-based, not configurable per org

Stages (`sourced → screened → shortlisted → interviewing → offered → placed → rejected → withdrawn`) are defined as a PostgreSQL enum type, not a per-org configuration table. This simplifies the pipeline board and ensures cross-org consistency. If agencies need custom stages, that's a v2 feature that would require migrating from enum to a stages table.

## D-25: Security tests use file scanning, not Flask imports

`test_security_verification.py` reads route source files as text and scans for dangerous patterns (debug endpoints, raw SQL, eval, bypass keywords). This avoids needing Flask installed in the test environment and catches patterns that runtime testing might miss. Trade-off: string scanning can have false positives, but the current patterns are precise enough.

## D-26: Feature flags are env-var based, not per-org

AI agent enablement uses `EXECFLEX_AI_*` environment variables (e.g. `EXECFLEX_AI_MATCH_RERANK=1`). Per-org flags would require a settings table, admin UI, and flag evaluation on every request. Cut for v1 — all orgs get the same flag state. Risk: can't A/B test or gradually roll out per-org. Mitigation: environment variables can be changed per-deployment.

## D-27: agentic-core pinned to recruitment-agents branch, not tagged release

ExecFlex consumes agentic-core's recruitment module via a branch reference, not a tagged release. Production consumption requires a proper v0.17.0 release through the two-consumer gate (transparency-platform must also pass). Tonight's branch pin is for development only.

## D-28: LLM re-rank is advisory, deterministic score remains authoritative

The MatchReRankAgent re-ranks candidates with reasoning but the deterministic composite score is the auditable, authoritative score. LLM output is logged as `ai_rerank` decision type and marked `ai_generated: true`. EU AI Act compliance requires the deterministic path to remain the decision basis.

## D-29: Screening summary agent does not replace heuristic scoring

The ScreeningSummaryAgent produces a structured assessment (strengths, gaps, flags, next_step) but does not replace the heuristic scoring state machine. Both run: heuristic produces the numeric score, LLM produces the qualitative summary. The human reviewer sees both.

## D-30: CV parser uses Haiku (extraction tier), JD generator uses Sonnet (drafting tier)

Model routing follows agentic-core's TaskType policy: EXTRACTION → Haiku ($1/$5 per Mtok), DRAFTING → Sonnet ($3/$15 per Mtok). CV parsing is mechanical extraction; JD generation requires creative output and quality control. Cost difference: ~3x per call.

## D-31: Question flow is data, not an LLM agent

The voice screening question flow module provides per-role configurable 5-question structures (general, technology, finance, executive) as pure data. No LLM call — the questions are static, curated by recruitment domain experts. The voice transport layer reads these questions; answers are later fed to the ScreeningSummaryAgent.

## D-32: All AI-generated content marked in UI with visual indicator

Frontend uses a Sparkles icon + "AI-Generated" badge on all AI-produced content (match rationale, screening summary, JD text). EU AI Act Art. 50 requires transparency when AI generates content users interact with. The indicator appears regardless of flag state — if content exists, it's marked.

## D-33: Agent service fails gracefully — never blocks the deterministic path

When an AI feature flag is on but the agent fails (API key missing, LLM error, timeout), the agent service returns `None` and the endpoint returns deterministic-only results. No user-visible error. Logged at ERROR level for ops. The deterministic path is never gated on LLM availability.

---

## Phase 3 — Estate Consolidation Decisions

## D-34: Compliance module replaces governance-platform AI functions with 5 clean agents

governance-platform's `ai_service.py` has 7 interleaved functions with hardcoded model names. Ported to agentic-core as `agents/compliance/` with a clear separation: 2 pure-logic modules (prohibited_practices, snapshot_scorer) and 3 LLM-powered agents (RiskSummaryAgent, ScoringEngineAgent, SnapshotGapsAgent). The scoring algorithm was rewritten — weights differ from the original. Pure-logic modules require no LLM calls and can run offline.

## D-35: Compliance module gated behind multi-consumer requirement

Production consumption requires all three test suites passing: transparency-platform (231), ExecFlex (217), and new compliance tests (131). This is stated in `agentic_core/agents/compliance/__init__.py` docstring. The gate prevents a compliance-module merge from breaking transparency-platform's consumption of agentic-core primitives.

## D-36: Transcript privacy toggle defaults to OFF (GDPR-safe)

Cara (hr-advisory-agent) admin transcript visibility defaults to `False`. Admins viewing other employees' conversations see `ConversationAggregateOut` (id, module_type, status, created_at, message_count, topic_summary) — no message content. This protects employee-initiated HR conversations (grievances, mental health) from employer surveillance. Toggle is per-company via `Company.settings["admin_transcript_visibility"]`.

## D-37: governance-platform RAG service has no agentic-core equivalent — documented, not ported

ChromaDB-based RAG (375-word chunks, 38-word overlap) used for Stage D assessment completion. Deliberate decision NOT to port tonight — RAG requires infrastructure decisions (vector store choice, embedding model, chunking strategy) that shouldn't be made under time pressure. Documented in PROD_CLEANUP.md as a prerequisite before governance-platform decommissioning.

## D-38: AI badges on all pending decision cards (Art. 14 transparency)

Added Sparkles icon + blue "AI" badge to all pending AI decision cards across AgencyDashboard, ScreeningReview, and ComplianceCentre. EU AI Act Art. 14 requires that human overseers can identify AI-generated outputs. The badge is visual-only (no functional gate) — it signals "this needs human review" without blocking workflow.

## D-39: EXECFLEX_AI_COMPLIANCE_CHECK flag controls all compliance agent endpoints

Added a sixth feature flag (`EXECFLEX_AI_COMPLIANCE_CHECK`) controlling `POST /ai/compliance/snapshot` and `POST /ai/compliance/prohibited-check`. Off by default. Independent of other AI flags — compliance can be enabled without enabling match re-rank or screening summary.

## D-40: Compliance snapshot endpoint runs both scorer and gaps agent in one call

`POST /ai/compliance/snapshot` runs `calculate_snapshot_score` (pure logic, always) and `snapshot_gaps` (LLM-powered, when flag on). Returns combined result. If the gaps agent fails, the response still includes the score with `gaps: null`. This keeps the endpoint useful even without LLM availability.

## D-41: Frontend compliance UI uses 5-question snapshot form, not full Stage D assessment

The AI Act Snapshot tab in ComplianceCentre uses a simplified 5-question form (uses_ai, business_functions, affects_people, in_eu, has_documentation) — not the full multi-stage assessment from governance-platform. This is intentional: the snapshot is a quick-check tool, not a compliance certification. Full assessment remains in governance-platform until a dedicated workflow is built.

---

## Session 2 — Security + Overhaul + ISO Decisions

## D-42: In-memory sliding-window rate limiter, not Redis-backed

governance-platform rate limiting uses an in-memory `RateLimiter` class with `threading.Lock`. Chosen over Redis to avoid adding infrastructure dependencies to a single-process deployment. Trade-off: rate limits reset on process restart, and don't work across multiple workers. Acceptable for current single-box deployment.

## D-43: Snapshot input validation uses allowlists, not schema validation

`_validate_answers()` checks each answer field against a static set of valid values (`VALID_AI_CHOICES`, `VALID_FUNCTIONS`, `VALID_YESNO`, `VALID_DOCS`). Chosen over JSON Schema or Pydantic validation because the answer structure is a flat dict of enum fields — a full validation library adds complexity without benefit. If the answer format changes, update the sets.

## D-44: Smoke test bypass disabled in production via env var check, not removed

The smoke test bypass header (`X-Smoke-Test`) is needed for CI/CD but is dangerous in production. Rather than removing it (breaking CI), added a production guard that checks `FLASK_ENV=production` or `APP_ENV=production` and ignores the bypass header in that case. This preserves the CI workflow while closing the production attack vector.

## D-45: governance-platform model routing via env vars, not agentic-core ModelRouter

The overhaul-2026-07 branch migrates from hardcoded `claude-opus-4-6` to `GOV_MODEL_HAIKU` / `GOV_MODEL_SONNET` env vars with sensible defaults. Did NOT import agentic-core's `ModelRouter` — governance-platform doesn't depend on agentic-core and adding that dependency for model routing alone would couple the standalone to the shared library prematurely. TaskType convention is followed (SYNTHESIS→Haiku, REASONING→Sonnet) without the import.

## D-46: PII sanitizer strips emails, phones, API keys from all log output

`SanitizerFilter` is a `logging.Filter` that applies regex substitution on every log record before emission. Strips email addresses, phone numbers, and API key prefixes. Applied to all handlers via `configure_logging()`. Trade-off: regex-based sanitization has edge cases (e.g., URLs containing @ signs), but false-positive redaction is safer than false-negative leakage.

## D-47: ISO SoA self-assessment — 15 "IMPLEMENTED" controls based on code evidence

Self-assessed 93 ISO 27001:2022 Annex A controls. "IMPLEMENTED" status assigned only when code evidence exists (e.g., A.8.28 secure coding → parameterised queries + input validation, verified by tests). An external auditor may disagree on status levels, particularly for controls where "implemented" means "the code does this" rather than "a formal policy mandates and monitors this."

## D-48: Risk register scores credentials-exposed as highest priority (R-001, score 15)

20 risks scored on L×I (1-5 scales). R-001 (credentials in git history) scored L=3 × I=5 = 15. This is the single highest risk. Six items scored 12: single-box deployment, no credential rotation, Cara privacy, DPIA, no MFA. Treatment plan recommends credential rotation within one week.

## D-49: ISO 42001 AI inventory counts 64 AI capabilities across 5 systems

Counted all AI capabilities: 57 agents (from CANONICAL_AGENT_COUNT) + 7 governance-platform AI functions = 64. Classified ExecFlex recruitment agents (AI-002) as high-risk under EU AI Act Annex III, 4(a). GovCompli AI service (AI-005) classified as low-risk since it assists with compliance assessment, not employment decisions.

## D-50: DFY pack list-numbering fix preserves original numbers via ListItem `value` param

transparency-platform's `dfy_pack_pdf.py` had a defect where `bulletType="1"` auto-numbered from 1, discarding the original numbering in agent-generated markdown. Fix captures the original number with `r"^(\d+)\. (.*)$"` and passes it as `value=num` to each `ListItem`. Also collects continuation lines into the preceding item to prevent multi-paragraph items from splitting into orphaned text blocks.
