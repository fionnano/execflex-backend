# ainm Marketplace — Vetting Methodology

The vetting is the moat. A leader is listed in the curated pool only after
passing an independent, structured assessment. This document is the public
methodology behind the **"Independently vetted"** badge.

## What the badge means — and does not

- **"Independently vetted"** means the leader completed a fixed, track-specific
  technical + leadership assessment and scored at or above the pass threshold
  under a consistent rubric.
- We make **no percentile or "top X%" claim.** The badge is a pass/fail
  attestation against a documented bar, not a ranking.

## The assessment

Every leader is vetted against **one of four tracks** — ML Platform, Data
Engineering, AI Product, or Applied Research — chosen to match their discipline.
Each track presents an identical, fixed set of **six questions in a fixed order**:
four track-specific technical questions and two shared leadership questions
(leadership + stakeholder management). Consistency of questions and order is a
deliberate fairness control (every leader in a track faces the same bar).

Questions probe for **specificity, quantified evidence, concrete systems and
ownership, and seniority** — not fluency or confidence.

## Scoring

Each response is scored 0–100. The overall score is the **weighted mean** across
competencies (technical questions weighted more heavily than leadership; weights
sum to 1.0 within a track). The **pass threshold is 70/100** → `vetting_status =
verified`.

Two scoring paths, same contract:

1. **AI-scored (default in production).** Built on the shared `agentic-core`
   library. Mechanical per-answer scoring is routed to **Haiku**
   (`claude-haiku-4-5`); the overall explainable rationale and pass/fail
   reasoning are routed to **Sonnet** (`claude-sonnet-4-5`) — matching
   agentic-core's model-routing policy (mechanical → Haiku, reasoning → Sonnet).
   Every AI vetting decision is written to the `ai_decision_log` (EU AI Act
   Art. 13 transparency), the same audit trail the recruiter console uses.

2. **Deterministic heuristic (fallback).** When the AI path is unavailable
   (no key, flag off, or an error), a transparent heuristic scores each answer on
   length, quantified evidence (presence of numbers), concreteness (domain and
   ownership terms), and first-person ownership. This guarantees the product and
   the test suite never depend on live LLM calls, and gives a defensible score if
   the model is down.

Both paths return the same structure: an overall score, a pass/fail, an
explainable rationale, and per-competency scores. The rationale is surfaced on
the leader's profile next to the badge so a hiring company can see *why* a leader
was verified.

## Human oversight & scope

- The AI score is **advisory**; a human operator reviews before a leader is
  promoted, consistent with EU AI Act Article 14 (human oversight in high-risk
  recruitment contexts).
- This is a **structured assessment, not a live proctored coding test.** The
  assessment-adapter seam (`execo-bridge/src/lib/assessment-adapter.ts`) is
  preserved so a real proctored provider (HackerRank/Codility/live technical
  interview) can plug in as an additional signal without changing this scoring
  contract. Integrating a real proctored tool is a documented later step
  (see SHIPPED.md).

## Bias & fairness controls

- Identical questions in identical order per track.
- Competency rubric fixed in advance; scoring rewards evidence and specificity,
  not style, accent, or confidence.
- No protected-characteristic questions are asked.
- Every AI decision is logged and auditable; scores are explainable.
