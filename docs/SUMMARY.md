# SUMMARY — ExecFlex v1 Rebuild

Ten decisions the owner most needs to review, ordered by how wrong I might be.

---

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

## Deliverables Completed

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
| Decisions log | `docs/DECISIONS.md` | Done (D-01 to D-25) |
| Demo script | `docs/DEMO_SCRIPT.md` | Done |
| Agency dashboard | `execo-bridge: src/pages/agency/AgencyDashboard.tsx` | Done |
| Job create/edit | `execo-bridge: src/pages/agency/JobForm.tsx` | Done |
| Jobs list | `execo-bridge: src/pages/agency/JobsList.tsx` | Done |
| Pipeline board | `execo-bridge: src/pages/agency/PipelineBoard.tsx` | Done |
| Candidate profile | `execo-bridge: src/pages/agency/CandidateProfile.tsx` | Done |
| Screening review queue | `execo-bridge: src/pages/agency/ScreeningReview.tsx` | Done |
| Compliance centre | `execo-bridge: src/pages/agency/ComplianceCentre.tsx` | Done |
| Talent pools browser | `execo-bridge: src/pages/agency/TalentPools.tsx` | Done |
| API client | `execo-bridge: src/lib/api-v1.ts` | Done |
| Agency layout | `execo-bridge: src/components/layout/AgencyLayout.tsx` | Done |
| Build verification | `vite build` passes (2782 modules, 0 errors) | Done |
| Test verification | 196 backend tests passing | Done |

## Test Summary

| Suite | Tests | Time |
|-------|-------|------|
| Matching engine | 42 | <0.1s |
| Screening state machine | 73 | <0.1s |
| Syndication | 52 | <0.1s |
| Compliance | 17 | <0.1s |
| Security verification | 12 | <0.1s |
| **Total** | **196** | **0.25s** |
