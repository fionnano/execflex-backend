# Extraction Pipeline Design

## Overview

This document describes a pipeline to extract an anonymised pay benchmarking dataset from the ExecFlex/Ainm Supabase database. The pipeline is designed but NOT approved for execution — see GDPR_QUESTIONS.md for legal gates that must be cleared first.

---

## Source → Target Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│ SOURCE (Supabase PostgreSQL)                                         │
│                                                                       │
│  people_profiles ──┐                                                  │
│  placements ───────┼──→ [Extract] ──→ [Normalize] ──→ [Anonymise]    │
│  opportunities ────┤                                                  │
│  organizations ────┘                                                  │
│                                                                       │
└─────────────────────────────────────────────────────────────────────┘
                                          │
                                          ▼
┌─────────────────────────────────────────────────────────────────────┐
│ TARGET (CSV / JSON)                                                   │
│                                                                       │
│  benchmark_records.csv                                                │
│  - One row per compensation data point                                │
│  - No direct identifiers                                              │
│  - Quasi-identifiers generalized to k≥5                               │
│  - Suppressed rows where k<5                                          │
│                                                                       │
│  extraction_report.json                                               │
│  - Record counts, suppression stats, quality metrics                  │
│                                                                       │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Pipeline Stages

### Stage 1: Extract

Pull raw data from three source tables (read-only queries):

**Query 1 — Profile-stated compensation:**
```sql
SELECT
    p.id,
    p.headline,
    p.years_experience,
    p.industries,
    p.expertise,
    p.location,
    p.availability_type,
    p.is_ned_available,
    p.rate_range,
    p.profile_source,
    p.created_at
FROM people_profiles p
WHERE p.approved = true
  AND p.rate_range IS NOT NULL
  AND p.consent_given = true;
```

**Query 2 — Placement-confirmed compensation:**
```sql
SELECT
    pl.id,
    pl.role_title,
    pl.annual_salary,
    pl.fee_percentage,
    pl.placed_at,
    o.industry AS opportunity_industry,
    o.location AS opportunity_location,
    o.is_remote,
    o.commitment_type,
    org.industry AS company_industry,
    org.size AS company_size,
    org.location AS company_location
FROM placements pl
LEFT JOIN opportunities o ON pl.opportunity_id = o.id
LEFT JOIN organizations org ON pl.organization_id = org.id
WHERE pl.status IN ('invoiced', 'paid');
```

**Query 3 — Opportunity-stated compensation:**
```sql
SELECT
    o.id,
    o.title,
    o.compensation,
    o.industry,
    o.location,
    o.is_remote,
    o.commitment_type,
    o.type,
    org.size AS company_size,
    o.created_at
FROM opportunities o
LEFT JOIN organizations org ON o.organization_id = org.id
WHERE o.compensation IS NOT NULL
  AND o.status = 'open';
```

### Stage 2: Normalize

Transform raw data into a consistent intermediate format:

| Transformation | Logic |
|---------------|-------|
| **Role taxonomy** | Map free-text `headline`/`role_title` to ~20 standard categories using keyword matching (CEO, CFO, CTO, COO, CMO, CHRO, VP Engineering, Director, NED, etc.) |
| **Seniority band** | Derive from role taxonomy: C-suite, VP, Director, Senior Manager, Manager |
| **Experience band** | `years_experience` → 5-year bands: 5-10, 10-15, 15-20, 20-25, 25+ |
| **Location region** | Map free-text location to: Ireland, UK, EU, US, Other (keyword matching on country/city names) |
| **Company size band** | Map `size` field to: startup (<50), SME (50-250), mid-market (250-2000), enterprise (2000+) |
| **Compensation parsing** | Parse `rate_range` JSON: extract min, max, currency. Parse free-text `compensation` via regex for numeric ranges + currency symbols. |
| **Compensation rounding** | Annual salary: round to nearest €5,000. Daily rate: round to nearest €50. Hourly rate: round to nearest €10. |
| **Currency normalization** | Convert all to EUR using fixed reference rates (not live — snapshot at extraction time) |
| **Temporal coarsening** | Dates → quarter (e.g., "2025-Q3") |

### Stage 3: Anonymise

Apply k-anonymity with k=5:

1. Define quasi-identifiers: `role_category`, `seniority_band`, `experience_band`, `industry`, `location_region`, `engagement_type`, `company_size_band`
2. Group records by all quasi-identifier combinations
3. Suppress any group with fewer than k=5 records
4. For remaining groups, include compensation data
5. Generate synthetic record IDs (not traceable to source)

### Stage 4: Output

Produce:
- `benchmark_records.csv` — the anonymised dataset
- `extraction_report.json` — metadata (counts, suppression rate, coverage by dimension)

---

## Target Schema

```
benchmark_record:
  record_id:            string   # Synthetic UUID
  data_source:          enum     # profile_stated | placement_actual | opportunity_listed
  role_category:        enum     # CEO | CFO | CTO | COO | CMO | CHRO | CRO | NED | VP_Engineering | VP_Sales | VP_Product | Director_Finance | Director_Operations | Director_Technology | Senior_Manager | Manager | Consultant | Other
  seniority_band:       enum     # c_suite | vp | director | senior_manager | manager | other
  experience_band:      enum     # 5_10 | 10_15 | 15_20 | 20_25 | 25_plus
  industry:             enum     # (existing 16 industry enums)
  expertise_area:       enum     # (existing 7 expertise enums, nullable)
  location_region:      enum     # ireland | uk | eu | us | other
  engagement_type:      enum     # full_time | fractional | contract | ned | advisory
  is_remote:            boolean
  company_size_band:    enum     # startup | sme | mid_market | enterprise | unknown
  compensation_type:    enum     # annual_salary | daily_rate | hourly_rate
  compensation_min_eur: integer  # Rounded, EUR-normalized
  compensation_max_eur: integer  # Rounded, EUR-normalized
  placement_confirmed:  boolean  # True only for records from placements table
  record_quarter:       string   # e.g., "2025-Q3"
  k_group_size:         integer  # Number of records sharing this quasi-identifier combination
```

---

## Anonymisation Rules

| Rule | Implementation |
|------|---------------|
| **No direct identifiers** | Names, emails, phones, LinkedIn URLs, user IDs never extracted |
| **Location generalization** | City → Country/Region |
| **Experience generalization** | Exact years → 5-year bands |
| **Compensation rounding** | Salary to nearest €5k, rates to nearest €50/€10 |
| **Temporal coarsening** | Exact dates → quarterly |
| **k-Anonymity enforcement** | Suppress groups with <5 records |
| **No free-text fields** | `bio`, `notes`, `description` never included |
| **Synthetic IDs** | UUID v4 generated at extraction time |
| **No join keys** | Original `id`, `user_id`, `organization_id` never in output |

---

## Implementation

See `extract_benchmark.py` — a complete but disabled Python script that implements all stages. It uses the Supabase Python SDK for extraction and pandas for transformation/anonymisation.

The script:
- Connects to Supabase (requires `SUPABASE_URL` and `SUPABASE_SERVICE_KEY`)
- Executes the three source queries
- Applies normalization (role taxonomy, banding, rounding)
- Enforces k-anonymity (k=5)
- Outputs CSV + JSON report
- Includes synthetic-data tests that validate anonymisation without touching real data

---

## Risks and Limitations

| Risk | Mitigation |
|------|------------|
| Small dataset → high suppression rate | Accept lower coverage; do not reduce k below 5 |
| Free-text compensation parsing errors | Manual review of unparseable records; conservative exclusion |
| Currency conversion staleness | Document exchange rates used; re-run with updated rates before publication |
| Role taxonomy misclassification | Include "Other" category; manual review of unmatched titles |
| Motivated adversary with industry knowledge | Irish C-suite market is small; k=5 may not be sufficient for CEO/CFO roles in specific industries. Consider k=10 for C-suite categories or further generalization. |
| `placement_confirmed` flag as re-identification signal | If only 1-2 placements exist for a specific role/industry/quarter, the confirmed flag narrows identification. Apply k-anonymity *including* this dimension. |
