# SUMMARY — rebuild-core

Ten decisions the owner most needs to review, ordered by how wrong I might be.

---

## 1. Heuristic scoring is length-based only (D-06) — HIGH UNCERTAINTY

The screening state machine scores answers by character count (empty=1, <10=2, <50=3, <200=3.5, 200+=4). This means "yes" scores the same as "no, absolutely not" and a rambling irrelevant paragraph scores 4.0. It works for testing the state machine plumbing, but it will produce meaningless screening recommendations on real conversations. **The LLM scoring replacement is critical for production use.**

Risk: If this ships to real users before LLM scoring is added, screening recommendations will be arbitrary and agencies will lose trust immediately.

## 2. Skills matching is set overlap, not semantic (D-02) — HIGH UNCERTAINTY

`_score_skills` does case-insensitive exact set intersection between candidate skills and role required_skills. "Python" matches "python" but "Python development" does not match "Python". "ML" does not match "machine learning". No synonym expansion, no embedding similarity, no taxonomic mapping.

Risk: In real use, this will produce false negatives on nearly every search where the candidate and role don't use identical terminology.

## 3. Dimension weights are invented, not empirically validated (D-02) — MEDIUM UNCERTAINTY

The 7 weights (skills=0.25, industry=0.20, experience=0.15, etc.) are based on general recruiter sentiment from market research, not on placement outcome data. There's no feedback loop. In production, these weights might need to be radically different — or client-configurable.

Risk: The matching engine consistently ranks the wrong candidates first, creating more work than it saves.

## 4. The NED penalty (x0.3) and passive/closed multipliers are guesses (D-12, D-13) — MEDIUM UNCERTAINTY

The multiplicative penalties (NED mismatch=0.3, passive=0.85, closed=0.1) were chosen to "feel right" but have no empirical basis. A NED penalty of 0.3 might be too harsh (blocking good fractional candidates) or too lenient (letting non-NED candidates rank too high).

Risk: Agencies placing NED/fractional roles get poor results from the matching engine and bypass it.

## 5. Client intake brief extraction is regex/delimiter-based (D-11) — MEDIUM UNCERTAINTY

`build_brief()` splits requirements by comma, takes the first sentence as role title, etc. Real client answers won't be comma-delimited lists — they'll be conversational. "We need someone who knows Python, but also has leadership experience — oh, and they should be comfortable with ambiguity" won't parse cleanly.

Risk: Structured briefs from client intake are garbage and require manual correction, negating the automation value.

## 6. Screening fact extraction is keyword matching (D-06) — MEDIUM UNCERTAINTY

`_extract_facts()` uses regex for years of experience and substring matching for availability types. It will miss "a decade in fintech" (no digit), "I've been doing this since 2014" (no "years"), and most natural language compensation expressions.

Risk: Extracted facts are incomplete and unreliable, reducing the value of the screening → matching pipeline.

## 7. Consent-first flow might be too rigid for warm-transfer scenarios (D-05) — LOW-MEDIUM UNCERTAINTY

Every session starts with a GDPR consent prompt. If a candidate was already briefed on recording/GDPR by the recruiter before the AI call, asking again may feel redundant and create friction. The state machine has no "pre-consented" path.

Risk: Recruiters want to skip consent for warm transfers, and the system doesn't support it without code changes.

## 8. Handoff distress phrases may be too aggressive (D-07) — LOW-MEDIUM UNCERTAINTY

The word "legal" in "I have legal experience" triggers handoff. "Unfair" in "I think the market is unfair right now" triggers handoff. The distress detection is substring-based with no context awareness.

Risk: False-positive handoffs interrupt legitimate screening sessions, frustrating candidates and wasting recruiter time.

## 9. New matching engine coexists with old one indefinitely (D-01) — LOW UNCERTAINTY

The old `match_finder.py` stays alive for the existing `/match` endpoint. This means two matching engines with different scoring models. If someone asks "why does the API give different results than the CRM," the answer is "two engines."

Risk: Confusion during transition period. Manageable, but needs to be planned.

## 10. Voice monitor is disabled by default in dev but enabled by default in production (D-08) — LOW UNCERTAINTY

`VOICE_MONITOR_ENABLED` defaults to `true`, so deploying without the env var starts the monitor. This is correct for production but means any dev deployment without a `.env` file will start making probe requests.

Risk: Minor — dev deployments might generate noise in logs, but probes will fail harmlessly without the production URL.

---

## Deliverables Completed

| Item | Location | Status |
|------|----------|--------|
| MARKET_SCOPE.md | `docs/MARKET_SCOPE.md` | Done |
| GAP_ANALYSIS.md (with WEDGE) | `docs/GAP_ANALYSIS.md` | Done |
| TARGET_ARCHITECTURE.md | `docs/TARGET_ARCHITECTURE.md` | Done |
| Matching Engine v1 | `services/matching/` | Done — 42 green tests |
| Screening State Machine v1 | `services/screening/` | Done — 73 green tests |
| Voice monitor disable flag | `config/app_config.py` + `routes/voice_monitor.py` | Done |
| DECISIONS.md | `docs/DECISIONS.md` | Done |
| SUMMARY.md | `docs/SUMMARY.md` | Done |

## Files Created/Modified

**New files:**
- `services/matching/__init__.py`
- `services/matching/models.py`
- `services/matching/engine.py`
- `services/screening/__init__.py`
- `services/screening/models.py`
- `services/screening/state_machine.py`
- `services/screening/voice_interface.py`
- `test/test_matching_engine.py` (50 synthetic candidates, 20 roles, 42 tests)
- `test/test_screening_state_machine.py` (73 tests)
- `docs/MARKET_SCOPE.md`
- `docs/GAP_ANALYSIS.md`
- `docs/TARGET_ARCHITECTURE.md`
- `docs/DECISIONS.md`
- `docs/SUMMARY.md`

**Modified files:**
- `config/app_config.py` — added `VOICE_MONITOR_ENABLED`
- `routes/voice_monitor.py` — gated thread start behind config flag

**Files NOT touched:**
- `modules/match_finder.py` — untouched, backward compat preserved
- `services/realtime_session_state.py` — untouched, voice pipeline preserved
- All Twilio/voice/audio pipeline files — untouched per hard constraint
