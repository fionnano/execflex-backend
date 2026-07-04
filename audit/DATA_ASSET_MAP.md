# Data Asset Map

Audit date: 2026-07-04
Source: Code-level analysis only. No database queries executed. Schema reconstructed from Supabase migrations, SDK usage patterns, and TypeScript type definitions.

---

## Dataset Overview

The platform's ~11k data points are distributed across a recruitment lifecycle: candidate profiles, job opportunities, AI-driven matching, voice screening interactions, and completed placements with salary data.

**Estimated volume breakdown** (inferred from code patterns, not verified):

| Table | Estimated Records | Basis |
|-------|-------------------|-------|
| `people_profiles` | 1,000–3,000 | Candidate/NED profiles (LinkedIn imports, bulk uploads, signups) |
| `opportunities` | 500–1,500 | Job postings, retained searches |
| `interactions` | 3,000–8,000 | Screening calls, emails, follow-ups (append-only) |
| `placements` | 100–500 | Completed placements with salary data |
| `outbound_call_jobs` | 2,000–5,000 | Call queue entries (includes retries) |
| `match_suggestions` | 2,000–10,000 | Computed AI matches |
| `interaction_turns` | 10,000–30,000 | Per-turn transcript data |

---

## Table-by-Table Data Model

### 1. people_profiles — Executive/Candidate Records

The core data asset. Each record represents a candidate, NED, or fractional executive.

| Field | Type | Personal Data? | Benchmarking Value |
|-------|------|----------------|-------------------|
| `id` | UUID | No | Join key |
| `user_id` | UUID (FK auth.users) | Indirect | N/A |
| `first_name` | text | YES | No — must be removed |
| `last_name` | text | YES | No — must be removed |
| `headline` | text | Quasi-identifier | Yes — role title for benchmarking |
| `bio` | text | Possibly | No — too free-form |
| `location` | text | Quasi-identifier | Yes — geographic benchmarking |
| `timezone` | text | No | Supporting |
| `years_experience` | integer | No | YES — core benchmark dimension |
| `industries` | text[] (enum) | No | YES — core benchmark dimension |
| `expertise` | text[] (enum) | No | YES — core benchmark dimension |
| `skills` | text[] | No | Yes — secondary dimension |
| `languages` | text[] | No | Supporting |
| `availability_type` | enum | No | YES — engagement model (fractional/full-time/contract) |
| `rate_range` | JSONB {min, max, currency} | No | YES — compensation benchmark data |
| `is_ned_available` | boolean | No | Yes — NED segment |
| `linkedin_profile_url` | text | YES | No — must be removed |
| `linkedin_member_id` | text | YES | No — must be removed |
| `linkedin_connected_at` | timestamp | No | No |
| `headshot_url` | text | YES | No — must be removed |
| `approved` | boolean | No | Quality filter |
| `profile_source` | text | No | Yes — source channel analysis |
| `source_metadata` | JSONB | Possibly | No — may contain PII from enrichment |
| `consent_given` | boolean | No | GDPR compliance flag |
| `consent_given_at` | timestamp | No | GDPR audit |
| `created_at` | timestamp | No | Temporal analysis |

**Validation logic** (from code):
- `rate_range`: JSON with `min`, `max` (numeric), `currency` (text, typically EUR/GBP/USD)
- `industries`: constrained to 16 enums (Technology, Financial Services, Healthcare, etc.)
- `expertise`: constrained to 7 enums (Strategy, Operations, Finance, Technology, Marketing, Sales, HR)
- `availability_type`: enum (full_time, part_time, fractional, contract)
- Phone numbers: E.164 normalized via trigger
- Emails: lowercased via trigger

**Data quality risks**:
- `headline` is free-text — inconsistent formatting for role titles
- `location` is free-text — no geocoding standardization
- `rate_range` may be missing (nullable) or use inconsistent currencies
- `bio` may contain personal information embedded in free text
- No mandatory fields enforced at DB level beyond `user_id`

### 2. opportunities — Job Postings

| Field | Type | Personal Data? | Benchmarking Value |
|-------|------|----------------|-------------------|
| `id` | UUID | No | Join key |
| `title` | text | No | YES — role title |
| `description` | text | No | Supporting — requirements, context |
| `industry` | text | No | YES — industry segment |
| `location` | text | No | YES — geographic dimension |
| `is_remote` | boolean | No | YES — remote work premium |
| `commitment_type` | text | No | YES — engagement model |
| `compensation` | text | No | YES — stated comp range (free-text) |
| `type` | enum | No | YES — opportunity category |
| `status` | enum | No | Filter (open/retained) |
| `organization_id` | UUID (FK) | No | Company segment (via join) |
| `metadata` | JSONB | Possibly | Variable |

**Data quality risks**:
- `compensation` is free-text, not structured — e.g. "€120k-€150k" vs "$200/hr" vs "Competitive"
- No standardized currency or format
- `description` may mention specific companies or individuals

### 3. placements — Completed Placements (Highest-Value Benchmark Data)

| Field | Type | Personal Data? | Benchmarking Value |
|-------|------|----------------|-------------------|
| `id` | UUID | No | Join key |
| `organization_id` | UUID (FK) | Indirect | Company segment |
| `opportunity_id` | UUID (FK) | No | Role context |
| `candidate_user_id` | UUID | YES (indirect) | Must be removed |
| `role_title` | text | No | YES — actual placement title |
| `annual_salary` | NUMERIC(12,2) | No | YES — THE core benchmark value |
| `fee_percentage` | NUMERIC(5,2) | No | Yes — market fee rates |
| `fee_amount` | NUMERIC(12,2) | No | Yes — absolute fee |
| `status` | enum | No | Filter (pending/invoiced/paid) |
| `placed_at` | timestamp | No | YES — temporal trend |
| `notes` | text | Possibly | No — may contain PII |
| `created_at` | timestamp | No | Temporal |

**Data quality risks**:
- `annual_salary` is high-quality structured data — likely the most reliable benchmark field
- `fee_percentage` standard in recruitment is 15-30% — validates data integrity
- Small dataset (estimated 100-500 records) — may not meet k-anonymity thresholds alone

### 4. organizations — Company Records

| Field | Type | Personal Data? | Benchmarking Value |
|-------|------|----------------|-------------------|
| `name` | text | No (company) | YES — company segment |
| `industry` | text | No | YES — industry dimension |
| `size` | text | No | YES — company size band |
| `location` | text | No | YES — geographic dimension |
| `website` | text | No | Supporting |

### 5. interactions — Screening Call Records

| Field | Type | Personal Data? | Benchmarking Value |
|-------|------|----------------|-------------------|
| `transcript_text` | text | YES | No — contains PII |
| `screening_scores` | JSONB | No | YES — candidate quality signals |
| `screening_recommendation` | text | No | Yes — outcome data |
| `artifacts` | JSONB | Possibly | Variable — may contain extracted profile data |

The `artifacts` JSONB field is populated by post-call LLM extraction and may contain structured candidate data (experience, skills, salary expectations mentioned during calls).

---

## Anonymised Benchmarking Extract — Proposed Schema

### Target: Pay Benchmarking Dataset

A fully anonymised extract suitable for salary benchmarking would contain:

```
benchmark_record:
  - record_id: synthetic UUID (not traceable to source)
  - data_source: enum (profile_stated | placement_actual | call_extracted)
  - role_category: normalized role title (from headline/role_title → taxonomy)
  - seniority_band: enum (C-suite | VP | Director | Senior Manager | Manager)
  - years_experience_band: enum (5-10 | 10-15 | 15-20 | 20-25 | 25+)
  - industry: enum (from existing 16 industry enums)
  - expertise_area: enum (from existing 7 expertise enums)
  - location_region: enum (Ireland | UK | EU | US | Other) — coarsened
  - engagement_type: enum (full_time | fractional | contract | NED)
  - is_remote: boolean
  - company_size_band: enum (startup | SME | mid-market | enterprise)
  - compensation_type: enum (annual_salary | daily_rate | hourly_rate)
  - compensation_min: numeric (rounded to nearest 5000/50)
  - compensation_max: numeric (rounded to nearest 5000/50)
  - compensation_currency: enum (EUR | GBP | USD)
  - placement_confirmed: boolean (from placements table vs. stated rate)
  - record_date_quarter: text (e.g. "2025-Q3") — coarsened temporal
```

### Aggregation and k-Anonymity

| Dimension | Granularity | Notes |
|-----------|-------------|-------|
| Role title | Mapped to ~20 categories (CEO, CFO, CTO, COO, CMO, CHRO, NED, VP Eng, etc.) | Free-text `headline` requires NLP normalization |
| Experience | 5-year bands | Prevents unique identification |
| Location | Country/region only | City-level is too identifying for senior executives |
| Industry | Keep existing 16 enums | Sufficient granularity |
| Company size | 4 bands | Company name never included |
| Compensation | Rounded to nearest €5,000 (salary) or €50 (day rate) | Prevents exact-match re-identification |
| Time | Quarterly | Monthly is too granular for small datasets |

**k-Anonymity threshold**: k ≥ 5 (each combination of quasi-identifiers must have at least 5 records). With ~1,000-3,000 profiles, many cross-tabulations will fall below this threshold — expect significant suppression.

**Suppression estimate**: With 20 role categories × 5 experience bands × 16 industries × 4 regions × 4 engagement types = 25,600 possible cells. With ~3,000 records, average cell occupancy is 0.12. Even with heavy dimension reduction, expect 60-80% of cells to have fewer than 5 records and require suppression or generalization.

---

## Data Quality Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| `rate_range` is self-reported, not verified | HIGH | Cross-reference with `placements.annual_salary` where available |
| `compensation` in opportunities is free-text | HIGH | Requires NLP parsing + manual review |
| `headline` is inconsistent (CEO vs "Chief Executive Officer" vs "Founder & CEO") | MEDIUM | Role taxonomy mapping needed |
| `location` is free-text (Dublin vs "Dublin, Ireland" vs "Co. Dublin") | MEDIUM | Geocoding/normalization needed |
| Mixed currencies in `rate_range` | MEDIUM | Normalize to single currency at extraction time |
| Small dataset may not support meaningful benchmarks | HIGH | Placements alone (~100-500) may be too few for statistical significance |
| No validation that `rate_range` reflects market reality | MEDIUM | Outlier detection needed |
| `bio` and `notes` fields may contain embedded salary/PII | LOW | Excluded from extract |
| `source_metadata` from PDL enrichment may contain PII | MEDIUM | Excluded from extract |

---

## Personal Data Classification Summary

### Direct Identifiers (must be removed for any reuse)
- `first_name`, `last_name`
- `linkedin_profile_url`, `linkedin_member_id`
- `headshot_url`
- `channel_identities.value` (phone, email)
- `candidate_user_id` (in placements)
- `transcript_text` (in interactions)

### Quasi-Identifiers (may enable re-identification when combined)
- `headline` (especially at C-suite level — "CEO of [specific company]")
- `location` (city-level for senior executives in small markets)
- `years_experience` (exact value)
- `placed_at` (exact date)
- `annual_salary` (exact amount for a specific role/date/location)

### Safe for Benchmarking (after aggregation)
- `industries`, `expertise` (enum values)
- `availability_type`
- `rate_range` (after rounding)
- `is_ned_available`
- `years_experience` (banded)
- `annual_salary` (rounded)
- `fee_percentage` (industry-standard ranges)

---

## What the ~11k Dataset Likely Contains Per Record

Based on code analysis, a typical `people_profiles` record contains:
1. **Identity**: first name, last name, LinkedIn URL, headshot
2. **Professional profile**: headline (role title), bio, years of experience
3. **Classification**: industries (1-3), expertise areas (1-3), skills (free list)
4. **Availability**: type (fractional/full-time/etc.), NED availability flag
5. **Compensation**: rate range (min/max/currency) — self-reported
6. **Location**: free-text location, timezone
7. **Source**: how they entered (signup, bulk upload, LinkedIn import, PDL enrichment)
8. **Consent**: consent flag + timestamp

For records that also have placement data:
9. **Actual salary**: confirmed annual salary at placement
10. **Fee**: percentage and absolute amount
11. **Company**: organization size, industry, location (via join)
12. **Date**: when placed

For records with screening calls:
13. **Screening scores**: JSON with assessment metrics
14. **Call artifacts**: LLM-extracted structured data from conversation
