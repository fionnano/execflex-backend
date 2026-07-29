# ainm AI Act Check — Methodology

**This is an EU AI Act readiness and decision-support assessment, not legal
advice.** It is generated from your answers using a documented, mostly rule-based
methodology. It does not create a legal opinion or guarantee compliance. For
binding conclusions, consult qualified legal counsel. This disclaimer appears in
the product UI (on the question set and every result) and is returned by the API.

The check is practitioner-built and EU AI Act aware: the risk classification,
obligations, and gap analysis are **deterministic and explainable** — not
model-generated — so a reviewer can trace exactly why each conclusion was
reached. An optional AI-generated narrative may accompany the result and is
always visibly marked as AI-generated.

## What it does

A company answers a short, staged set of questions about one AI system (with a
hiring/HR emphasis) and receives:

- a **risk tier** under the EU AI Act — Unacceptable / High / Limited / Minimal;
- the **obligations** they are subject to, article by article, each with a plain
  requirement and a practical deployer action;
- a **gap list** — what's missing in governance, oversight, data, and records;
- a **readiness score (0–100)** with an explainable rationale;
- a clear **decision** — ready to proceed / further review / significant gaps.

## The staged question set

Four stages (agentic-core `compliance/question_set.py`, stable ids):

1. **AI inventory & context** — what system, whether you use AI, which business
   functions (HR/finance/legal are high-risk areas under Annex III).
2. **Prohibited-practice screen (Article 5)** — a direct screen for the practices
   the Act bans outright (subliminal/manipulative techniques, exploiting
   vulnerabilities, social scoring, emotion inference at work, real-time public
   biometric ID, biometric categorisation).
3. **Scope & people impact** — whether the AI affects people's outcomes, whether
   it filters/ranks/scores candidates (Annex III point 4), EU jurisdiction, and
   whether affected people are informed.
4. **Governance, oversight & documentation** — human review, data-quality/bias
   controls, record-keeping, and whether documentation exists.

## The engine

Built on the shared agentic-core compliance module (ported from the
governance-platform engine), consumed by execflex-backend `services/aiact`.

### 1. Prohibited-practice screen (Article 5) — deterministic
`check_prohibited_practices` evaluates the Stage 2 answers against Article 5
hard-stops and high-risk indicators. Any hard-stop or prohibited flag forces the
tier to **Unacceptable Risk** and caps the readiness score.

### 2. Risk classification — deterministic rules
`services/aiact/engine.classify_risk`:
- prohibited hard-stop / prohibited → **Unacceptable Risk**;
- no active AI use → **Minimal Risk**;
- biometric identification/categorisation, OR automated hiring decisions, OR a
  high-risk function (HR/finance/legal) that affects people → **High Risk**
  (Annex III);
- otherwise affects people / interacts with people → **Limited Risk**;
- else → **Minimal Risk**.

### 3. Readiness score — deterministic
`calculate_snapshot_score` produces a 0–100 compliance score from the weighted
risk factors (function risk, people impact, EU jurisdiction, documentation).
Prohibited caps the score at ≤15; high-risk at ≤60, so a light self-report never
reads falsely green for a genuinely high-risk system.

### 4. Obligation mapping — deterministic
`obligations.map_obligations` maps the tier + context to the concrete Articles
that apply, each with a requirement and a practical deployer action:
- **High Risk** → the full stack: Art 9 (risk management), Art 10 (data
  governance), Art 11/Annex IV (technical documentation), Art 12 (record-keeping),
  Art 13 (transparency/instructions), Art 14 (human oversight), Art 15
  (accuracy/robustness/security), Art 26 (deployer obligations); plus Art 26(7)
  (inform workers & representatives) and Annex III(4) for employment use; Art 27
  (FRIA) conditionally.
- **Limited Risk** → Art 50 transparency (disclose AI; label AI content); disclose
  AI in decisions where people are affected; keep light documentation.
- **Minimal Risk** → voluntary codes of conduct (Art 95); Art 50 where people
  interact directly.
- **Unacceptable** → Art 5: halt and seek legal review.

### 5. Gap analysis — deterministic
`deterministic_gaps` flags specific missing controls against the answers, each
citing its Article (human oversight → Art 14; data governance → Art 10; logging →
Art 12; documentation → Art 11/Annex IV; informing people → Art 26(7)/50).

### 6. Optional AI narrative — clearly marked
When `AIACT_AI` is enabled and a key is present, the agentic-core
`ScoringEngineAgent` (Sonnet, via the platform's model routing) writes an
explainable summary and recommendations. This narrative is the **only**
AI-generated part of the result; it carries `ai_generated=true`, is rendered with
an "✨ AI-generated" marker in the UI, and is logged to `ai_decision_log`
(decision_type=`ai_act_readiness`) for transparency (Article 13). On any failure
the deterministic rationale stands in — the product never depends on live tokens.

## EU AI Act provisions referenced

Article 5 (prohibited practices), Article 6 & Annex III (high-risk, incl. point 4
employment), Articles 9, 10, 11 (Annex IV), 12, 13, 14, 15 (high-risk
requirements), Article 26 & 26(7) (deployer obligations, worker information),
Article 27 (fundamental-rights impact assessment), Article 43 (conformity
assessment), Article 50 (transparency), Article 95 (codes of conduct).

## Human oversight, transparency & scope

- Conclusions are **advisory**. Classification, obligations, and gaps are
  deterministic and explainable; the AI narrative is advisory and marked.
- Every AI-generated conclusion is visually marked and logged.
- Assessments are **org-scoped** — a company sees only its own — and can be
  **exported or deleted** (GDPR).
- This is a readiness/decision-support tool. It does **not** perform a formal
  conformity assessment (Article 43) and is **not** legal advice.
