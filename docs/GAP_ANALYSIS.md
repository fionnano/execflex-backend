# Gap Analysis: ExecFlex vs Recruitment CRM Market

**Date:** 2026-07-04
**Branch:** rebuild-core
**Inputs:** MARKET_SCOPE.md (competitor taxonomy), codebase feature audit (12-category deep scan)

---

## Current State Summary

ExecFlex is an **AI-powered executive sourcing + screening platform**, not a recruitment CRM. It excels at finding candidates (PDL sourcing), screening them (voice AI + GPT-4o scoring), and connecting them to roles (auto-match + outreach). It lacks the workflow, pipeline, search, and multi-tenancy features that define a CRM.

---

## Gap Matrix

### Legend
- **Have (Strong):** Feature exists and is competitive or best-in-class
- **Have (Basic):** Feature exists but below market standard
- **Stub:** Code/table exists but not functional
- **Missing:** Not implemented at all

| Feature | Status | Market Expectation | Priority | Notes |
|---------|--------|-------------------|----------|-------|
| **Voice AI Screening** | Have (Strong) | Not expected | — | Unique. No competitor offers this. |
| **AI Screening Scoring** | Have (Strong) | Not expected | — | GPT-4o rubric scoring with EU AI Act compliance |
| **Auto-match + Outreach** | Have (Strong) | Expected (basic) | — | LLM-personalized emails, daemon-triggered on role post |
| **Shortlist Sharing** | Have (Strong) | Expected | — | Public URL, view tracking, intro requests |
| **Candidate Upload** | Have (Strong) | Expected | — | CSV/XLSX, 40+ header aliases, LinkedIn export detection |
| **PDL Enrichment** | Have (Basic) | Expected | — | Email/phone enrichment from LinkedIn URL |
| **Email/Outreach** | Have (Basic) | Expected (sequences) | P2 | No drip sequences, no open/click tracking |
| **Client Management** | Have (Basic) | Expected (full CRM) | P2 | Upload + campaigns only, no relationship tracking |
| **Billing/Placements** | Have (Basic) | Expected (invoicing) | P2 | Retainers + placement fee calc, no invoice generation |
| **Revenue Reporting** | Have (Basic) | Expected (dashboards) | P3 | Aggregate counts, no time-series or conversion funnels |
| **Matching Engine** | Have (Basic) | Expected (AI) | **P0** | Token overlap scoring, no semantic matching, no explain |
| **Multi-tenancy** | Stub | **Required** | **P0** | Org table exists, no code enforcement, no teams/roles |
| **Pipeline/Workflow** | Missing | **Required** | **P0** | No stages, no transitions, no kanban |
| **Candidate Search** | Missing | **Required** | **P1** | No list view, no search, no filters beyond /match |
| **Boolean Search** | Missing | Expected | P1 | No AND/OR/NOT operators |
| **Interview Scheduling** | Missing | Expected | P2 | No calendar integration |
| **Resume Parsing** | Missing | Expected | P2 | No CV parsing (rely on structured upload + PDL) |
| **Job Board Distribution** | Missing | Expected | P3 | No multi-board posting |
| **Mobile App** | Missing | Expected | P3 | No native or PWA mobile experience |
| **Candidate Portal** | Stub | Expected | P3 | Screening status page exists (token-based, 90-day expiry) |

---

## P0 Gaps: Must Close Before Launch

### 1. Matching Engine (upgrade from basic to competitive)

**Current:** Token-overlap integer scoring (0–8 range). Fixed weights. No semantic understanding. No explanation of *why* a candidate matched.

**Market standard:** AI-ranked results with confidence scores, multi-signal weighting, saved searches.

**Target:** Deterministic multi-signal scoring with configurable weights + optional LLM re-rank for semantic context. Every result includes a human-readable "why this match" explanation. Scored 0–100.

**Implementation:** `services/matching/engine.py` — new module, does not replace `modules/match_finder.py` (which stays for backward compat on existing endpoints).

### 2. Multi-tenancy (rebuild from stub to real)

**Current:** `organizations` table with `stripe_customer_id`. No org_id filtering in application code. Relies entirely on Supabase RLS. No team members, no user roles beyond admin/authenticated.

**Market standard:** Full org isolation, team member management, role-based access (admin/manager/recruiter/viewer), record-level permissions.

**Target (v1):** Enforce `org_id` on all queries at application layer. Add `user_role` concept (owner, recruiter, viewer). Defer team invites to v2.

**Implementation:** Deferred — architectural design in TARGET_ARCHITECTURE.md. Not built in this sprint.

### 3. Pipeline/Workflow (build from scratch)

**Current:** Only `approved` boolean flag and `screening_recommendation` enum. No stage tracking.

**Market standard:** Configurable pipeline stages, kanban view, stage transitions with triggers, activity timeline.

**Target (v1):** Fixed stages (sourced → screened → shortlisted → interviewing → offered → placed → rejected). Stage transitions logged with timestamp and actor. No kanban UI (backend only this sprint).

**Implementation:** Deferred — data model in TARGET_ARCHITECTURE.md. Not built in this sprint.

---

## P1 Gaps: Close in Next Quarter

### 4. Candidate Search
Add full-text search across `people_profiles` with filters (industry, location, experience, skills, availability). Supabase supports `to_tsvector` / `ts_query` natively.

### 5. Boolean Search
Layer AND/OR/NOT on top of full-text search. Parse user query into structured filter.

---

## P2–P3 Gaps: Backlog

- **Email sequences** (drip campaigns, scheduled sends, open/click tracking)
- **Interview scheduling** (calendar integration, availability polling)
- **Resume parsing** (Textkernel or custom, extract structured data from PDF/DOCX)
- **Client CRM depth** (relationship tracking, deals, company hierarchy)
- **Invoice generation** (PDF invoices from placement records)
- **Job board distribution** (Indeed, LinkedIn, ZipRecruiter APIs)
- **Mobile app** (React Native or PWA)
- **Advanced reporting** (time-series, funnels, cohort analysis)

---

## WEDGE: Where ExecFlex Wins

The wedge is the combination of capabilities no competitor offers together:

### Primary Wedge: AI-First Screening Pipeline

```
Role posted → Auto-match candidates → Voice AI screens top matches → 
Scored shortlist (with rubric + compliance) → Shared with client
```

**Why it's a wedge:**
1. **Zero competitors offer built-in voice screening.** Bullhorn, Vincere, JobAdder, Recruit CRM, and Loxo all require external tools (HireVue, Spark Hire, or manual calls) for candidate assessment. ExecFlex does it natively.

2. **The screening produces structured, scored output.** Not just "pass/fail" — per-question rubric scores, competency ratings, weighted recommendations, and EU AI Act compliant bias audits. This data feeds the matching engine, creating a feedback loop competitors can't replicate without building their own voice AI.

3. **End-to-end automation.** A recruiter posts a role and gets a scored shortlist without making a single phone call. The competitors' best case: post a role → manually source → manually screen → manually score. ExecFlex: post → auto-source → auto-screen → scored shortlist.

4. **Compliance as moat.** EU AI Act Articles 50/86 compliance is built in (bias audits, right to explanation, transparency). Competitors will need 12–18 months to retrofit this.

### Secondary Wedge: Explainable Matching

The rebuilt matching engine (this sprint) adds:
- **Dimension-level scoring:** Each match criterion scored independently (industry: 85/100, skills: 72/100, location: 95/100)
- **"Why this match" narrative:** Human-readable explanation generated from score breakdown
- **Optional LLM re-rank:** GPT-4o can re-order results considering context that token matching misses (e.g., "10 years at McKinsey" implies strategy expertise even if "strategy" isn't in their skills list)
- **Screening data integration:** Candidates who've been voice-screened get a quality signal boost

### Wedge Positioning Statement

> ExecFlex is the only recruitment platform where posting a role automatically sources, screens, and scores candidates — delivering a compliance-ready shortlist in hours, not weeks. Agencies using Bullhorn or Vincere still make every screening call manually.

### Wedge Validation Criteria

The wedge holds if:
1. Voice screening quality is good enough that agencies trust it for first-round filtering (not final decisions)
2. Auto-match accuracy is high enough that >50% of auto-sourced candidates are relevant
3. Time-to-shortlist is measurably faster than manual process (target: same day vs. 5–10 days)
4. EU AI Act compliance is a genuine buyer concern (likely yes by 2027 enforcement)

The wedge fails if:
1. Bullhorn/Vincere acquire a voice AI vendor and integrate natively (18+ month timeline)
2. Agencies refuse to trust AI screening for any stage of the process (mitigate with transparency + human-in-loop)
3. Voice AI quality (OpenAI Realtime) degrades or pricing becomes prohibitive
