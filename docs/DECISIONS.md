# Decisions Log — rebuild-core

Decisions made during autonomous Phase 4 build. Numbered for reference.

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
