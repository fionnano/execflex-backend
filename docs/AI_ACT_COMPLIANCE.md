# AI Act Compliance Mapping — ExecFlex v1

**Date:** 2026-07-04
**Status:** Implementation complete (v1 backend)
**Classification:** HIGH-RISK AI system under EU AI Act Annex III, point 4 (employment, workers management, access to self-employment)

---

## Applicable Obligations

ExecFlex uses AI for candidate screening and matching in recruitment contexts. Under the EU AI Act (Regulation 2024/1689), this qualifies as a HIGH-RISK AI system. The following obligations apply:

---

## 1. Transparency (Art. 50)

**Obligation:** Persons interacting with an AI system must be informed they are interacting with AI, unless obvious from context.

**Implementation:**
| Feature | Location | Mechanism |
|---------|----------|-----------|
| Voice screening disclosure | `services/screening/state_machine.py` | GDPR/AI consent prompt at session start — "this conversation may be recorded and analysed by AI" |
| Public AI notice | `GET /api/v1/compliance/ai-notice` | Publicly accessible notice describing all AI uses |
| Candidate notification | `screening_sessions.consent_given` field | Consent captured and persisted per session |

**Verification:** Test `TestAINotice` in `test_compliance.py` confirms notice covers voice screening, matching, scoring, and candidate rights.

---

## 2. Human Oversight (Art. 14)

**Obligation:** High-risk AI systems shall be designed so natural persons can effectively oversee them. No fully automated decisions with legal/significant effects without human review.

**Implementation:**
| Feature | Location | Mechanism |
|---------|----------|-----------|
| Human review gate on reject | `services/compliance/human_review.py` | `require_human_review_for_reject()` blocks automated rejection — requires authenticated human + reason |
| Decision review queue | `GET /api/v1/compliance/decisions?unreviewed=true` | Surfaces all unreviewed AI decisions |
| Review endpoint | `POST /api/v1/compliance/decisions/<id>/review` | Human approves or overrides with reason |
| Override logging | `ai_decision_log.human_override`, `override_reason` | Every override recorded with justification |

**Verification:** Tests `TestHumanReviewGate` prove: no context → blocked, no reason → blocked, valid human + reason → allowed.

---

## 3. Record-Keeping / Logging (Art. 12)

**Obligation:** High-risk AI systems shall support automatic logging of events relevant to identifying risks and facilitating post-market monitoring.

**Implementation:**
| Feature | Location | Mechanism |
|---------|----------|-----------|
| AI decision log | `ai_decision_log` table | Every AI decision logged with: type, inputs, model, score, explanation, dimension scores, human review status |
| Decision types | `screening_score`, `match_rank`, `stage_change`, `reject`, `shortlist`, `auto_match` | Comprehensive taxonomy |
| Pipeline events | `pipeline_events` table | Every stage transition logged with actor, reason, timestamp |
| Activity log | `activity_log` table | CRM-wide activity feed with entity context |
| Screening transitions | `screening_sessions.transitions` JSONB | Full state machine transition history per session |

**Verification:** Security tests in `test_security_verification.py` verify all data queries include org_id isolation.

---

## 4. Data Governance (Art. 10)

**Obligation:** Training, validation, and testing datasets shall be relevant, representative, and as free of errors and biases as possible.

**Implementation:**
| Feature | Location | Mechanism |
|---------|----------|-----------|
| Deterministic scoring | `services/matching/engine.py` | v1 uses rule-based scoring — no ML training data. When LLM scoring is added, this section must be updated. |
| Bias audit table | `screening_bias_audit` (existing) | Question consistency, score deviation, bias flags tracked |
| Synthetic test data | `test/test_matching_engine.py` | 50 candidates, 20 roles — diverse across gender, location, industry, experience |
| No real data in tests | Hard constraint | Zero real candidate/client data in any fixture |

---

## 5. Accuracy, Robustness, Cybersecurity (Art. 15)

**Obligation:** High-risk AI systems shall achieve appropriate accuracy, robustness, and cybersecurity.

**Implementation:**
| Feature | Location | Mechanism |
|---------|----------|-----------|
| Explainable scores | `MatchExplanation` dataclass | Per-dimension scores with human-readable reasons and composite summary |
| Score bounds | `MatchEngine.score_candidate()` | All scores clamped 0-100, weights auto-normalize |
| Org isolation | `services/api/auth.py` | org_id from JWT, never from request body |
| No debug endpoints | `routes/api_v1/` | S-001 eliminated — no debug routes exist |
| Parameterized queries | All route files | S-003 eliminated — no raw SQL, no string interpolation |
| Auth on all mutations | `@require_org` decorator | S-002 eliminated — no per-route bypass logic |

**Verification:** `test_security_verification.py` — 12 tests scan all v1 route source code for dangerous patterns.

---

## 6. Right to Explanation (GDPR Art. 22 + AI Act)

**Obligation:** Data subjects have the right not to be subject to automated decisions with legal/significant effects, and the right to obtain meaningful information about the logic involved.

**Implementation:**
| Feature | Location | Mechanism |
|---------|----------|-----------|
| Match explanation | `MatchResult.explanation.summary` | Human-readable "why this match" on every result |
| Dimension breakdown | `MatchExplanation.dimension_scores` | Per-dimension score + reason visible in API response |
| Screening feedback | `screening_feedback_requests` (existing) | Candidates can request explanation of screening outcome |
| No automated rejection | `require_human_review_for_reject()` | Human must review + provide reason before any reject decision |

---

## 7. Data Subject Rights (GDPR Art. 15-17)

**Obligation:** Right of access, right to rectification, right to erasure.

**Implementation:**
| Feature | Location | Mechanism |
|---------|----------|-----------|
| Data rights intake | `POST /api/v1/compliance/data-rights` | Public endpoint — no auth required, candidates submit access/erasure/rectification/portability requests |
| Request management | `data_rights_requests` table | Status tracking: pending → in_progress → completed/rejected |
| Admin processing | `PATCH /api/v1/compliance/data-rights/<id>` | Owner-only endpoint to process requests |
| Request viewer | `GET /api/v1/compliance/data-rights` | Dashboard view of all pending/completed requests |

---

## 8. Pay Transparency (EU Pay Transparency Directive 2023/970)

**Obligation:** Job postings must include pay range information.

**Implementation:**
| Feature | Location | Mechanism |
|---------|----------|-----------|
| Required pay range | `POST /api/v1/jobs` | `pay_range_min` and `pay_range_max` are required fields — API returns 400 if missing |
| Pay fields on schema | `opportunities` table | `pay_range_min`, `pay_range_max`, `pay_range_currency`, `pay_range_period` columns |

**Verification:** `TestPayTransparency` confirms pay range enforcement in job creation route.

---

## Compliance Gaps (to address before production)

| Gap | Risk | Mitigation Timeline |
|-----|------|---------------------|
| LLM scoring not yet implemented | Heuristic scoring is not AI — actual AI scoring needs Art. 10 data governance | Phase 2 |
| Bias audit on matching engine | No statistical fairness testing on match scores across protected characteristics | Phase 2 |
| Automated penetration testing | Security posture needs external validation | Pre-launch |
| DPIA (Data Protection Impact Assessment) | Required for high-risk processing — not yet completed | Pre-launch |
| Art. 49 EU Database registration | High-risk AI systems must be registered in the EU database | Pre-launch |
