# Decision Brief — ExecFlex Platform

One-page evidence summary for deciding between: (a) extract data for pay benchmarking, (b) revive the platform, (c) archive it.

---

## State of the System

**Working**: The backend runs on Render ($7-25/mo). Database is live on Supabase. Auth, API, and voice features are functional code. The Cara voice monitor runs every 5 minutes consuming OpenAI API credits.

**Not working**: The platform is dormant — no active users. Cara voice has an unresolved audio forwarding bug (event name mismatch from OpenAI beta→GA migration). Several security issues exist (unprotected debug endpoints, webhook verification gaps).

**Scale**: ~68 Python packages, ~130KB voice WebSocket file, 63 database migrations, 23 Supabase tables. Two repos (backend + frontend). Not trivial to pick up cold.

---

## Cost to Revive

| Item | Estimate | Notes |
|------|----------|-------|
| Fix critical security issues (S-001 to S-003) | 2-4 hours | Remove debug endpoints, enforce webhook secrets, fix filter injection |
| Fix Cara voice (event name rename) | 1-2 hours | Known issue, known fix |
| Remove diagnostic logging | 30 minutes | Commits c49274a, cleanup |
| QA pass across all features | 2-3 days | Voice, billing, matching, screening, admin |
| Frontend review + update dependencies | 1-2 days | React 18 is current, but UI state unknown |
| Operational hardening (rate limiting, monitoring) | 1-2 days | Redis rate limiting, proper alerting |
| **Total cold-start revival** | **5-10 days** | Assumes sole developer, no new features |

Standing monthly cost after revival: $50-150/mo (Render + Supabase Pro + Twilio/OpenAI pay-per-use).

---

## Cost to Extract and Archive

| Item | Estimate | Notes |
|------|----------|-------|
| Legal/GDPR review (external advisor) | 1-3 days + fee | Must clear Gates 1-3 in GDPR_QUESTIONS.md |
| Run extraction pipeline | 2-4 hours | Script is written but disabled pending legal review |
| Manual review of output for re-identification | 4-8 hours | Especially C-suite records in small markets |
| Archive repos (freeze branches, disable Render) | 1-2 hours | |
| **Total extraction + archive** | **2-5 days** + legal fee | |

One-time cost. Standing cost drops to $0/mo after archive (only domain renewals remain).

---

## Value and Risk of the Dataset

**Value**:
- Placement records with confirmed annual salaries — the highest-quality benchmark data (gold standard: actual accepted offers)
- Structured candidate profiles with self-reported rate ranges across Irish/UK executive market
- Industry/seniority/engagement-type dimensions already categorized
- Screening call data with AI-extracted insights (skills, expectations)

**Risks**:
- **Small dataset**: ~100-500 placements, ~1,000-3,000 profiles. After k-anonymity enforcement (k=5), expect 60-80% suppression. The output dataset may be too thin for meaningful benchmarking.
- **Re-identification**: Senior executives in the Irish market are a small, identifiable population. A "CFO, Financial Services, Dublin, 20+ years, €250k" record may be uniquely identifiable despite anonymisation.
- **Data quality**: Compensation is partly free-text (in opportunities), partly self-reported (in profiles). Only placements have verified amounts.
- **Legal**: Three GDPR gates must be cleared (lawful basis, purpose limitation, anonymisation sufficiency). Third-party data from PDL/LinkedIn may have downstream use restrictions.

---

## The Three GDPR Questions That Gate Everything

1. **Was the original collection purpose broad enough to cover benchmarking?** (Check the privacy notice candidates saw at signup.)

2. **Is the proposed anonymisation actually anonymous for senior Irish executives?** (k=5 may not be sufficient for C-suite in a small market.)

3. **Do PDL/LinkedIn data sharing agreements permit downstream use for benchmarking?** (Check DPA terms.)

If any gate fails, extraction cannot proceed lawfully without fresh consent (impractical for a dormant platform) or a different legal mechanism.

---

## What to Verify First on Waking

If you decide to revive or extract:

1. **Check Render billing** — confirm the service is still running and what it costs today
2. **Check Supabase plan** — confirm active plan tier and whether approaching limits
3. **Check the `consent_given` field** — what percentage of profiles have explicit consent?
4. **Locate the privacy notice** — what did candidates actually agree to?
5. **Kill the voice monitor** — it's consuming OpenAI credits continuously for no benefit while dormant
