# AI Management System — ISO/IEC 42001 Scaffold

**Organisation:** Ainm Technologies
**Date:** 2026-07-05
**Status:** DRAFT scaffold
**Standard:** ISO/IEC 42001:2023 — AI Management System

---

## 1. AI Policy Statement

Ainm Technologies develops and deploys AI systems across recruitment (ExecFlex), HR advisory (Ainm/Cara), governance compliance (GovCompli), and pay transparency. All AI systems are designed to:

1. Augment human decision-making, never replace it for consequential decisions
2. Operate transparently with clear AI-generated content labelling
3. Process personal data only with legal basis and appropriate safeguards
4. Fail gracefully — deterministic paths remain available when AI is unavailable
5. Be auditable — inputs, outputs, and model versions logged for every AI decision

## 2. AI System Inventory

| System ID | Name | Purpose | Risk Level | Model Provider | PII Processing |
|-----------|------|---------|-----------|----------------|----------------|
| AI-001 | DFY Pack Agents (11) | Generate pay transparency compliance packs | High | Anthropic Sonnet | Yes — employee pay data |
| AI-002 | ExecFlex Recruitment Agents (10) | CV parsing, match re-ranking, screening summary, JD generation | High | Anthropic Sonnet/Haiku + OpenAI Realtime | Yes — candidate data |
| AI-003 | Transparency Features (6) | Pay equity analysis, bias detection, gap analysis | High | Anthropic Sonnet/Haiku | Yes — employee pay data |
| AI-004 | Ainm Advisory Agents (30) | HR advice (Cara), BD outreach, content, compliance monitoring | Medium | Anthropic Sonnet/Haiku + OpenAI | Yes — HR conversations |
| AI-005 | GovCompli AI Service (7 functions) | EU AI Act assessment scoring, gap analysis, compliance chat | Low | Anthropic Haiku/Sonnet | No — assessment data only |

**Total: 57 AI agents + 7 AI functions = 64 AI capabilities**

## 3. Intended Purpose Documentation

### AI-001: DFY Pack Agents
- **Intended purpose:** Generate structured compliance documents for EU Pay Transparency Directive reporting
- **Users:** HR consultants reviewing AI-generated packs before client delivery
- **Not intended for:** Autonomous compliance certification without human review
- **Human oversight:** Consultant review queue with release gate (D-35 multi-consumer gate)

### AI-002: ExecFlex Recruitment Agents
- **Intended purpose:** Assist recruiters with candidate evaluation, not make hiring decisions
- **Users:** Recruitment agencies using ExecFlex platform
- **Not intended for:** Autonomous reject/accept decisions (D-17: human review gate blocks all terminal decisions)
- **Human oversight:** ScreeningReview page, AI badges on all AI-generated cards (D-38), deterministic score remains authoritative (D-28)

### AI-003: Transparency Features
- **Intended purpose:** Analyse pay data for gender gaps and bias indicators
- **Users:** HR professionals and pay equity consultants
- **Not intended for:** Legal determinations of pay discrimination
- **Human oversight:** All analysis presented as advisory with source data visible

### AI-004: Ainm Advisory Agents
- **Intended purpose:** Provide HR guidance, draft business communications, generate content
- **Users:** SME owners and HR managers via Cara voice/chat interface
- **Not intended for:** Legal advice, therapy, or medical guidance
- **Human oversight:** Conversation aggregates visible to company admin (D-36: transcript content OFF by default)

### AI-005: GovCompli AI Service
- **Intended purpose:** Score organisations against EU AI Act requirements and identify compliance gaps
- **Users:** Compliance officers and legal teams
- **Not intended for:** Formal regulatory compliance certification
- **Human oversight:** All scores accompanied by human-readable explanations; chat responses clearly labelled as AI-generated

## 4. Risk Assessment for AI Systems

### EU AI Act Classification

| System | Art. 6 High-Risk? | Justification |
|--------|-------------------|---------------|
| AI-002 (Recruitment) | **Yes** — Annex III, 4(a) | AI used in recruitment and selection of natural persons |
| AI-001 (Pay Transparency) | **Likely Yes** — Annex III, 4(b) | AI influencing decisions on terms of work-related relationships |
| AI-003 (Transparency Features) | **Likely Yes** — Annex III, 4(b) | AI analysing pay equity affecting employment terms |
| AI-004 (HR Advisory) | **Possibly** — Annex III, 4(a) | AI providing HR advice that could influence employment decisions |
| AI-005 (Compliance Scoring) | **No** | AI assisting with compliance assessment, not making employment decisions |

### AI-Specific Risks

| Risk ID | AI System | Risk | Mitigation | Status |
|---------|-----------|------|-----------|--------|
| AIR-001 | AI-002 | Bias in candidate scoring | Deterministic weights auditable (D-02); NED/passive multipliers documented (D-12, D-13) | PARTIAL |
| AIR-002 | AI-002 | LLM hallucination in screening summary | Summary is advisory; heuristic score authoritative (D-29) | MITIGATED |
| AIR-003 | AI-001 | Incorrect pay transparency analysis | Multi-stage pipeline with human review gate | MITIGATED |
| AIR-004 | AI-004 | Harmful HR advice (Cara) | Distress phrase detection + handoff (D-07); no legal/medical advice scope | PARTIAL |
| AIR-005 | AI-002 | Automated rejection without human review | Human review gate (D-17) blocks ALL terminal decisions | MITIGATED |
| AIR-006 | All | Prompt injection via user input | Input validation (FIX-1); AI output not used for system decisions | PARTIAL |
| AIR-007 | All | Model provider data retention | Anthropic: zero-retention API. OpenAI Realtime: review DPA terms | REQUIRES REVIEW |
| AIR-008 | AI-002 | Voice screening consent not obtained | CONSENT state mandatory (D-05); GDPR disclosure before proceeding | MITIGATED |

## 5. Human Oversight Register

| Control | Implementation | Evidence |
|---------|---------------|----------|
| AI content labelling | Sparkles + "AI-Generated" badge on all AI content (D-38) | ExecFlex frontend components |
| Human review for terminal decisions | `require_human_review_for_reject()` gate (D-17) | `services/compliance/human_review.py` |
| Fail-graceful AI | Agent service returns None on failure (D-33) | `services/ai/agent_service.py` |
| Feature flags for AI capabilities | 6 `EXECFLEX_AI_*` env vars, all OFF by default (D-26, D-39) | `services/ai/feature_flags.py` |
| Deterministic scoring as authority | Composite score is auditable; LLM re-rank advisory only (D-28) | `services/matching/engine.py` |
| AI decision logging | Full input/output/model logged per decision (D-20) | `ai_decision_log` table |
| Privacy toggle on AI conversations | Admin transcript visibility OFF by default (D-36) | `hr-advisory-agent` Company.settings |
| Consent-first for data subjects | CONSENT state mandatory before AI screening (D-05) | `services/screening/state_machine.py` |

## 6. AI Act Compliance Evidence

The governance-platform itself serves as evidence of the organisation's AI Act compliance methodology:

| Art. | Requirement | Evidence |
|------|------------|----------|
| Art. 5 | Prohibited practices | `prohibited_practices.py` — deterministic Art. 5 checker |
| Art. 6 | High-risk classification | This document, Section 4 |
| Art. 9 | Risk management | Risk register (iso/RISK_REGISTER.md), AI-specific risks above |
| Art. 10 | Data governance | Synthetic test data policy (D-10), PII classification in asset register |
| Art. 12 | Record-keeping | AI decision log (D-20), structured logging with PII sanitiser |
| Art. 13 | Transparency | AI badges (D-38), content labelling |
| Art. 14 | Human oversight | Human review gate (D-17), human oversight register above |
| Art. 15 | Accuracy, robustness, cybersecurity | Test suites (217+231+131+42), rate limiting (FIX-1, FIX-2) |
| Art. 50 | Transparency obligations | AI content marked in UI (D-32) |

## 7. Monitoring and Review

### Performance Monitoring

| Metric | Method | Frequency |
|--------|--------|-----------|
| AI agent error rate | Application logs | Continuous |
| Voice pipeline uptime | Cara voice prober (v0.16.4) | Every 5 minutes |
| Model cost per product | Anthropic/OpenAI billing dashboard | Monthly |
| Scoring distribution | ai_decision_log analytics | Quarterly |

### Review Schedule

| Review | Frequency | Trigger |
|--------|-----------|---------|
| AI system inventory | Quarterly | New agent added or removed |
| Risk assessment | Quarterly | Change to model, prompts, or scoring |
| Human oversight effectiveness | Semi-annually | Compliance audit |
| Full AIMS review | Annually | ISO 42001 maintenance cycle |

---

## Maintenance

This document should be updated whenever:
- New AI agents are added to any product
- Model providers or versions change
- EU AI Act implementing regulations are published
- An AI-related incident occurs
