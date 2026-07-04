# Audit Summary — Five Most Important Findings

## 1. The dataset is likely too small for meaningful anonymised benchmarking

With an estimated 100-500 placement records and 1,000-3,000 profiles, k-anonymity enforcement (k=5) will suppress 60-80% of records. The resulting dataset may lack the volume needed for statistically significant pay benchmarks, particularly when cross-tabulated by role × industry × region × seniority. The Irish executive market is small enough that even anonymised C-suite records may be re-identifiable.

## 2. Three GDPR gates block extraction — none can be answered from code alone

Before any data leaves the database: (a) confirm the original privacy notice covered analytics/benchmarking use, (b) verify that anonymisation defeats motivated re-identification of senior Irish executives, (c) check that PDL and LinkedIn data sharing agreements permit downstream use. All three require legal review, not engineering.

## 3. The voice monitor is burning OpenAI credits while the platform is dormant

The Cara voice uptime monitor (added in this session) runs a synthetic probe every 5 minutes. Each probe opens a full OpenAI Realtime session. At 288 probes/day, this is an estimated $5-15/month in pure waste if no users exist. Disable immediately if the platform stays dormant.

## 4. Critical security issues exist that must be fixed before any reactivation

Three CRITICAL findings: (a) unprotected debug endpoints expose call transcripts publicly at `/voice/debug/*`, (b) Stripe webhook verification is skipped when the secret is unconfigured — accepts forged payment events, (c) Supabase filter injection via string interpolation in screening.py. These are live risks if the backend is reachable.

## 5. Revival is feasible in 5-10 days but the value proposition is unclear

The codebase is functional, dependencies are current, and the architecture is sound. The main cost is QA/testing across voice, billing, and matching features after a dormancy period. The question isn't "can it be revived" but "should it be" — the standing cost ($50-150/mo) and ongoing maintenance effort may not be justified without a clear go-to-market plan.

---

## Documents Produced

| Document | Purpose |
|----------|---------|
| [ARCHITECTURE_AS_IS.md](ARCHITECTURE_AS_IS.md) | Full system map with Mermaid diagrams |
| [SECURITY_FINDINGS.md](SECURITY_FINDINGS.md) | 18 findings ranked by severity |
| [OPERATIONAL_FINDINGS.md](OPERATIONAL_FINDINGS.md) | Dead code, active costs, running services |
| [DATA_ASSET_MAP.md](DATA_ASSET_MAP.md) | Complete data model, field classification, benchmarking schema |
| [GDPR_QUESTIONS.md](GDPR_QUESTIONS.md) | Legal questions for advisor, three gating conditions |
| [EXTRACTION_DESIGN.md](EXTRACTION_DESIGN.md) | Pipeline design: source → anonymise → target |
| [extract_benchmark.py](extract_benchmark.py) | Complete but disabled extraction script with passing synthetic tests |
| [DECISION_BRIEF.md](DECISION_BRIEF.md) | One-page evidence summary for the revive/extract/archive decision |
| [DECISIONS.md](DECISIONS.md) | Assumptions log |
