# Target Architecture: ExecFlex Recruitment CRM

**Date:** 2026-07-04
**Branch:** rebuild-core
**Inputs:** ARCHITECTURE_AS_IS.md (audit), GAP_ANALYSIS.md (gaps + wedge), SECURITY_FINDINGS.md

---

## Design Principles

1. **Security by design.** The three critical findings (S-001 debug endpoints, S-002 Stripe bypass, S-003 filter injection) die in this architecture — they are not patched on the old code.
2. **Multi-tenancy from day one.** Every query includes `org_id`. No query runs without tenant context.
3. **AI-native, not AI-bolted.** Matching, screening, and scoring are core primitives, not add-ons.
4. **Deterministic first, LLM second.** Core logic must work without an LLM call. LLM enhances, never gates.
5. **Explainable.** Every AI-produced score includes a human-readable justification.
6. **GDPR/EU AI Act compliant.** Consent tracking, right to explanation, bias audits built into data model.

---

## System Boundary Diagram

```
                                    ┌─────────────────────────┐
                                    │   Frontend (execo-bridge)│
                                    │   React + Next.js        │
                                    └──────────┬──────────────┘
                                               │ HTTPS
                                    ┌──────────▼──────────────┐
                                    │      API Gateway         │
                                    │  (Flask blueprints)      │
                                    │  Auth middleware          │
                                    │  Org context injection    │
                                    │  Rate limiting            │
                                    └──────────┬──────────────┘
                              ┌────────────────┼────────────────┐
                              │                │                │
                    ┌─────────▼──────┐ ┌───────▼─────┐ ┌───────▼───────┐
                    │  Core Services  │ │Voice/Screen │ │  CRM Services │
                    │                 │ │             │ │               │
                    │ • MatchEngine   │ │ • Screening │ │ • Pipeline    │
                    │ • Scoring       │ │   StateMach │ │ • Candidates  │
                    │ • Ranking       │ │ • Voice WS  │ │ • Roles       │
                    │                 │ │ • Twilio     │ │ • Clients     │
                    └────────┬────────┘ └──────┬──────┘ │ • Shortlists  │
                             │                 │        │ • Placements  │
                             │                 │        └───────┬───────┘
                             │                 │                │
                    ┌────────▼─────────────────▼────────────────▼───────┐
                    │                  Data Layer                        │
                    │  Supabase PostgreSQL + RLS + Application-layer     │
                    │  org_id enforcement                                │
                    └──────────────────────────────────────────────────┘
```

---

## Module Architecture

### New Modules (this sprint)

```
services/
├── matching/
│   ├── __init__.py
│   ├── engine.py          # MatchEngine: deterministic multi-signal scoring
│   ├── models.py          # Candidate, Role, MatchResult, MatchExplanation
│   └── reranker.py        # Optional LLM re-rank (deferred — interface only)
│
├── screening/
│   ├── __init__.py
│   ├── state_machine.py   # ScreeningStateMachine: intake flows
│   ├── models.py          # ScreeningSession, Question, Answer, Outcome
│   └── voice_interface.py # VoiceInterface stub (matches Aidan/Twilio pattern)
```

### Existing Modules (unchanged)

```
modules/
├── match_finder.py        # Legacy matching — kept for backward compat on /match endpoint
├── email_sender.py        # SMTP email
│
services/
├── realtime_session_state.py  # Existing call state machine (CallPhase enum)
├── screening_service.py       # Existing Twilio-based screening
├── call_extraction_service.py # Post-call LLM extraction
├── auto_match_service.py      # Auto-match daemon
├── billing_service.py         # Stripe integration
├── outreach_service.py        # Email campaigns
├── voice_call_service.py      # Twilio voice calls
```

---

## Matching Engine v1

### Scoring Model

The engine scores candidates against a role using independent dimensions, each producing a 0–100 sub-score. Dimensions are weighted and combined into a final 0–100 composite score.

```
Dimensions:
  industry_fit      weight=0.20  — how well candidate's industries match role's industry
  skills_fit        weight=0.25  — expertise/skills overlap with role requirements
  experience_fit    weight=0.15  — years of experience vs role's requirement
  location_fit      weight=0.10  — geographic match (exact, same country, remote)
  availability_fit  weight=0.10  — availability type matches role's commitment type
  compensation_fit  weight=0.10  — candidate's rate expectations vs role's budget
  screening_fit     weight=0.10  — voice screening recommendation (if available)

Composite = Σ(dimension_score × weight)
```

### Explainability

Every MatchResult includes:
```python
@dataclass
class MatchExplanation:
    dimension_scores: Dict[str, DimensionScore]  # {dimension: {score, reason}}
    composite_score: float                        # 0-100
    summary: str                                  # "Strong match: deep fintech expertise 
                                                  #  and 15 years experience exceed the 
                                                  #  10-year minimum. Location mismatch 
                                                  #  (Dublin vs London) partially offset 
                                                  #  by remote availability."
```

The summary is generated from score patterns — no LLM call required. Templates cover common patterns (strong match, partial match, weak match, specific dimension highlights).

### LLM Re-rank (interface only, not built this sprint)

```python
class Reranker(Protocol):
    def rerank(self, matches: List[MatchResult], role: Role, context: str) -> List[MatchResult]:
        """Re-order matches using LLM semantic understanding."""
        ...
```

The engine accepts an optional `reranker` parameter. When provided, deterministic scoring runs first, then the reranker adjusts ordering. The deterministic score is always preserved — the reranker can only change the order, not the scores.

---

## Screening State Machine v1

### State Diagram

```
                    ┌─────────┐
                    │  IDLE   │
                    └────┬────┘
                         │ start_session(type=candidate|client)
                         ▼
                    ┌─────────┐
                    │ CONSENT │ ←── Recording/AI disclosure
                    └────┬────┘     Must acknowledge before proceeding
                         │ consent_given=true
                         ▼
                    ┌─────────┐
                    │ INTAKE  │ ←── Question flow runs here
                    └────┬────┘     Questions differ by session type
                    ┌────┴────┐
                    │         │
              answer_all    handoff_triggered
                    │         │
                    ▼         ▼
              ┌──────────┐ ┌──────────┐
              │ SCORING  │ │ HANDOFF  │ ←── Transferred to human
              └────┬─────┘ └──────────┘
                   │ scoring_complete
                   ▼
              ┌──────────┐
              │ COMPLETE │ ←── Structured output ready
              └──────────┘
```

### Session Types

**Candidate Intake:**
- Purpose: Screen a candidate for a specific role or general talent network
- Question categories: background, experience, skills, motivation, availability, compensation, consent
- Output: ScreeningOutcome with per-question scores, recommendation, extracted UserFacts

**Client Intake:**
- Purpose: Capture a hiring brief from a client/employer
- Question categories: role description, requirements, team, timeline, budget, culture
- Output: StructuredBrief with role spec, must-haves, nice-to-haves, deal-breakers

### Handoff Triggers

The state machine transitions to HANDOFF when:
1. Candidate explicitly requests to speak to a human
2. Candidate expresses distress or legal concern
3. Question loop exceeds max turns without progress
4. Confidence in extracted data falls below threshold

### Voice Interface

The voice layer is **stubbed behind an interface** matching the Aidan/Twilio pattern:

```python
class VoiceInterface(Protocol):
    def send_message(self, session_id: str, text: str) -> None: ...
    def end_session(self, session_id: str) -> None: ...
    def get_transcript(self, session_id: str) -> List[Turn]: ...
```

The existing `realtime_session_state.py` CallPhase enum maps to the screening states:
- `CONNECTING` → `IDLE`
- `GREETING` → `CONSENT`
- `DISCOVERY` → `INTAKE`
- `CLOSING` → `SCORING` / `COMPLETE`
- `ENDED` → terminal

---

## Multi-tenancy Target (design only — not built this sprint)

### Application-Layer Enforcement

Every service function receives `org_id` as a required parameter. No query runs without it.

```python
# Target pattern (not yet implemented):
def get_candidates(org_id: str, filters: CandidateFilters) -> List[Candidate]:
    return db.table("people_profiles") \
        .select("*") \
        .eq("organization_id", org_id) \
        .execute()
```

### User Roles

```
owner      — full access, billing, team management
recruiter  — CRUD on candidates/roles/clients within org, run screenings
viewer     — read-only access to shortlists and reports
```

### Data Isolation

- Application-layer: all queries filter by `org_id`
- Database-layer: Supabase RLS policies as defense-in-depth (not sole mechanism)
- API-layer: `org_id` injected from authenticated user's JWT claims, never from request body

---

## Pipeline Target (design only — not built this sprint)

### Stage Model

```sql
-- Target schema (not yet migrated):
CREATE TYPE pipeline_stage AS ENUM (
    'sourced',
    'screened',
    'shortlisted',
    'interviewing',
    'offered',
    'placed',
    'rejected',
    'withdrawn'
);

ALTER TABLE people_profiles ADD COLUMN pipeline_stage pipeline_stage DEFAULT 'sourced';
ALTER TABLE people_profiles ADD COLUMN stage_changed_at timestamptz;
ALTER TABLE people_profiles ADD COLUMN stage_changed_by uuid REFERENCES auth.users(id);
```

### Stage Transitions

Transitions logged to `pipeline_events` table:
```sql
CREATE TABLE pipeline_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    candidate_id uuid REFERENCES people_profiles(id),
    opportunity_id uuid REFERENCES opportunities(id),
    from_stage pipeline_stage,
    to_stage pipeline_stage,
    changed_by uuid REFERENCES auth.users(id),
    reason text,
    created_at timestamptz DEFAULT now()
);
```

---

## Security Architecture (replaces audit findings)

### S-001 (Debug endpoints) → Eliminated
Debug/diagnostic routes do not exist in target architecture. No `/health/debug`, no prompt dumps, no raw log endpoints. Health checks return only `{"status": "ok", "version": "..."}`.

### S-002 (Stripe bypass) → Eliminated
Subscription validation moves to middleware. Every route that requires a paid tier checks subscription status via `@require_subscription(tier="growth")` decorator. No per-route bypass logic.

### S-003 (Filter injection) → Eliminated
All Supabase queries use parameterized filters via the Python SDK's `.eq()`, `.in_()`, `.gte()` methods. No raw SQL, no string interpolation into filters. The existing `match_finder.py` token parsing is safe (splits on regex, no eval), but new code uses typed filter objects.

### Authentication Flow (target)
```
Request → JWT verification (Supabase JWT secret) → Extract user_id + org_id → 
Inject into request context → Route handler receives typed context
```

No `@require_admin` with hardcoded email checks. Admin status stored in `user_roles` table.

---

## Migration Path

### Phase 1 (this sprint): Core primitives
- [x] Matching engine v1 (new module, doesn't touch legacy)
- [x] Screening state machine v1 (new module, interfaces with existing voice)
- [x] Disable voice monitor via config flag

### Phase 2 (next sprint): Data model
- [ ] Add `pipeline_stage` to `people_profiles`
- [ ] Create `pipeline_events` table
- [ ] Add `organization_id` FK to all relevant tables
- [ ] Create `user_roles` table
- [ ] Migrate existing data to pipeline stages (all approved → 'sourced', screened → 'screened')

### Phase 3: API layer
- [ ] Add org_id middleware
- [ ] Refactor routes to use org_id context
- [ ] Add pipeline endpoints (move stage, get pipeline, filter by stage)
- [ ] Add candidate search endpoint (full-text + filters)
- [ ] Remove debug endpoints (S-001)
- [ ] Add subscription middleware (S-002)

### Phase 4: Integration
- [ ] Wire screening state machine to existing Twilio/OpenAI voice pipeline
- [ ] Wire matching engine to new pipeline stage triggers
- [ ] Add email sequences (drip campaigns)
- [ ] Add client relationship tracking

### Phase 5: Polish
- [ ] Reporting dashboards (time-series, funnels)
- [ ] Candidate portal improvements
- [ ] Job board distribution (if validated)
- [ ] Mobile experience

---

## Technology Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Database | Keep Supabase PostgreSQL | Works, has RLS, 63 migrations invested |
| Backend | Keep Flask + gunicorn | Works, voice pipeline depends on it |
| New modules | Pure Python, no new frameworks | Minimize dependency surface |
| Matching | Deterministic + optional LLM | Must work offline, LLM enhances |
| State machine | Pure Python enum + transitions | Simple, testable, no framework needed |
| Multi-tenancy | App-layer + RLS | Belt and suspenders |
| Search | Supabase pg_trgm + tsvector | No external search service needed |
| Voice | Keep OpenAI Realtime GA | Working, paid, no reason to switch |
| Frontend | React + Next.js (execo-bridge) | Exists, not rebuilt this sprint |
