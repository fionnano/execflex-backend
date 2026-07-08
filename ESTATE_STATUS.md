# ESTATE STATUS — Sales-Claims Source of Truth

**Generated:** 2026-07-07
**Method:** Six independent skeptical audits, one per repo. Every claim verified against actual git state, test execution, and code — **no trust given to any prior self-report** (README / SUMMARY / DECISIONS / WHAT_CHANGED), including claims made by earlier AI sessions.

## Reading rules (read before you quote anything to a customer)

- **LIVE AND VERIFIED** = real code path, green tests locally, on the deployed branch, deploy config present. You can demo it.
- **BUILT BUT UNDEPLOYED** = real, tested code on main/master, but no production host is running it.
- **PARKED ON A BRANCH** = real work that is NOT merged; it does not exist in what ships.
- **CLAIMED BUT UNTESTED** = a capability some doc says "works" but NO real-path (non-mocked) test backs it. **Do not sell these.**

**Two hard caveats that cap every "LIVE" claim below:**
1. **Host reachability was NOT verified.** The audit had no access to the production hosts. Every "deployed / live" statement is INFERRED from deploy config + git state. Before a demo, confirm the host is actually serving (`curl` the URL / check the box).
2. **No repo makes a real LLM call in its default test suite.** Every AI agent across the estate is tested with a **mocked** Anthropic client returning canned output. Orchestration, parsing, retry, and guard logic are tested; **the quality of what Claude actually produces is tested nowhere.** Any "the AI does X well" claim must be shown live, not cited from tests.

---

## Estate at a glance

| Repo | Branch / HEAD | Tag | Tests (verified) | Deployed to | Prod status |
|---|---|---|---|---|---|
| **hr-advisory-agent** (ainm.ai) | `cara-privacy` / `117b73c` | none | **328 pass** | ainm.ai (Hetzner 91.98.19.73) | INFERRED LIVE |
| **transparency-platform** | `master` / `3d8c28d` | none | **238 pass, 1 skip** | transparency.ainm.ai (same box) | INFERRED LIVE |
| **execo-bridge** (ExecFlex FE) | `main` / `7c4032a` | none | **0 (no test suite)** | execflex.ai (same box) | INFERRED LIVE |
| **execflex-backend** (ExecFlex BE) | `main` / `ef3f7fa` | none | **217 pass** | execflex-backend-1.onrender.com | INFERRED deployed, AI broken |
| **agentic-core** (library) | `main` / `e58281b` | **v0.17.0** | **782 pass, 4 skip** | (not standalone — pinned into transparency) | pin is v0.15/0.16, NOT 0.17 |
| **governance-platform** | `main` / `3447d37` | none | **51 pass** | nowhere (docker-compose only) | NOT DEPLOYED |

All three ainm-branded products (ainm.ai, transparency.ainm.ai, execflex.ai) deploy to the **same Hetzner host 91.98.19.73** via manual `deploy.sh` over SSH. **No repo has CI/CD.** No repo except agentic-core has a release tag.

---

## ✅ LIVE AND VERIFIED — demoable today (real code, green tests, deployed branch)

> Host-liveness still INFERRED (caveat 1). These are the things whose *code paths are real and tested*, on the branch that deploys.

**hr-advisory-agent / ainm.ai** — the flagship, and the strongest-tested repo in the estate:
- FastAPI backend, 64 feature modules, **328 real-path tests green** (real in-memory DB, mocks confined to Stripe/SMTP/Anthropic edges).
- Multi-tenant isolation / IDOR defense, billing-tier gating, pay-equity logic, consent/GDPR, rate limiting, upload security, field-level crypto, prompt-injection defense — all real-path tested.
- **LLM resilience layer** (circuit breaker, transport fail-fast, Cara graceful-degradation stream) genuinely tested against a simulated outage — this is real, not mock theater.
- React web app builds; deploy.sh runs a **live smoke test against ainm.ai** on every deploy.

**transparency-platform / transparency.ainm.ai:**
- **238 tests green.** Pay-equity analysis (EIGE/IBEC pathways), gender pay-gap + comparator groups, job-evaluation, consultant review queue, multi-tenant scoping, auth + demo read-only — all real-path against seeded SQLite.
- **DFY-pack PDF rendering including the list-numbering fix** — real reportlab rendering, 8 genuine tests. The fix **is on master** (commit `116769e`), not parked.
- Stripe webhook + Resend email handler logic tested (external SDK mocked at the boundary — appropriate).

**execflex-backend (deterministic core only):**
- **217 tests green.** The **7-dimension matching engine** (real scoring + ranking over synthetic candidates) and the **screening state machine** (consent → intake → heuristic scoring → distress-handoff) are genuinely real-path and confirmed **zero-LLM** — the "deterministic" claim holds.
- Syndication **XML feed generation** (LinkedIn/Indeed/IrishJobs/Google) is real — but see caveat under CLAIMED (it does not post to live boards).

**execo-bridge (real-backend-ready screens only):** Dashboard, Jobs list, Job create/edit form (with AI JD generation + EU prohibited-check calls), Pipeline board (drag-to-move), Compliance Centre (AI-decision review + data-rights), Talent Pools. These hit backend routes that actually exist. *(Zero automated tests — "works" = last manual click-through, see BUILT/UNTESTED.)*

**governance-platform (deterministic logic only) — but NOT deployed (see next column):** snapshot scorer (18 tests), Article-5 prohibited-practices engine (13), logging PII sanitizer (6), rate limiter (5), Haiku-thinking guard branch logic (9, mocked client but real branching). Real and green — just not live anywhere.

---

## 🔧 BUILT BUT UNDEPLOYED — real & tested, but no prod host running it

- **governance-platform, entire repo.** 51 green tests, clean merges, Haiku guard verified verbatim in main — but there is **no CI, no managed-host config, no deployment anywhere.** Only `docker-compose.prod.yml` for self-hosting. Nothing here is in production. Do not tell anyone the governance platform is "live."
- **execflex-backend `/api/v1/*` new code — deployed but functionally BROKEN in prod for AI.** `agentic_core` **and** the `anthropic` SDK are **absent from `requirements.txt`**. On Render, every AI agent call (rerank, screening summary, CV parser, JD generator) hits `ImportError` and silently returns `None`. The deterministic API works; **all AI features are dead in prod until two packages are added to requirements and the deploy is re-verified.**
- **execflex-backend production security fix.** The smoke-test-bypass production guard is **BUILT but NOT on main** (see PARKED). Prod currently authenticates the bypass header if the secret is set.
- **hr-advisory-agent cold-send / cold-IMAP outbound engine.** Fully built, shipped **"(DARK)"** (deliberately deactivated), and sitting on **8 unpushed commits on local `main`** (origin/main does not have them). Real code, not running, not even pushed.

---

## 🌿 PARKED ON A BRANCH — NOT in what ships

| Repo | Branch | Ahead | What it is | Risk |
|---|---|---|---|---|
| **execflex-backend** | `security-hardening` | 2 commits | **Production smoke-bypass guard** (`FLASK_ENV/APP_ENV` check in `auth_helpers.py`) + SECURITY_CLOSURE.md | **HIGH — prod is unprotected without it.** WHAT_CHANGED.md wrongly implied this shipped. |
| **execflex-backend** | `audit-2026-07` | 1 commit | Audit doc only | none |
| **execo-bridge** | `audit-2026-07` | 1 commit | Cross-reference doc only | none |
| **hr-advisory-agent** | `cara-privacy` (checked out) | 1 commit over local main / 9 over origin | GDPR transcript-privacy toggle | low — but it's the current HEAD, unmerged |
| **hr-advisory-agent** | local `main` | 8 commits over origin/main | cold-send DARK series (unpushed) | release-hygiene: remote ≠ local |

Everywhere else, branches are stale already-merged pointers (agentic-core `recruitment-agents`, transparency `pack-content-fixes-platform` / `per-section-degradation`, execo-bridge `edit/edt-*`) — **0 commits ahead, nothing parked.**

---

## 🚩 CLAIMED BUT UNTESTED — do NOT put these in a deck

Every item below is asserted somewhere in a self-report doc but has **no real-path test**. Flagged by repo:

**Cross-estate (all repos):**
- **"The AI produces good output"** — for *every* LLM agent in the estate (Cara, DFY-pack agents, match rerank, screening summary, CV parser, JD generator, snapshot gaps, compliance chat). All tested with mocked clients returning canned data. **No test exercises a real Claude generation.** The only real-LLM tests that exist (agentic-core's 4-test tier) were **skipped**.

**agentic-core:**
- **`voice/` and `hr_general/` agent domains** — claimed as architecture layers; both are **empty `__init__.py` stubs with zero code.**
- **DFY-pack end-to-end generation** — "11 steps, byte-identical" is tested with **every agent mocked**; no real full pack has been generated in the suite.
- **"67 AI agents across the estate"** (CANONICAL_AGENT_COUNT.md) — unverifiable marketing; this repo has ~17 LLM-routing modules. Treat the 67 as a slogan, not a fact.
- Stale self-report: SUMMARY.md cites a non-existent branch and a wrong test count (447 vs real **782**).

**transparency-platform:**
- **"Bias checker"** (README) — no identifiable test.
- **Compliance dashboard PDF-with-AI-summary** — only the *cache* behavior is tested; the real AI+render path is stubbed on both ends.
- agentic-core version drift: installed **0.17.0** but `requirements.txt` pins **0.16.4**.

**execflex-backend:**
- **AI agents (rerank / screening summary / CV parser / JD generator)** — only "returns None when flag off / no key" is tested. No agent is ever invoked with a real response. **And they can't run in prod anyway** (missing packages, above).
- **Runtime tenant isolation** — claimed enforced (D-16); only **grep-verified** (test asserts strings absent from source). No test sends a cross-org request and confirms a 403.
- **Live job-board syndication** — adapters generate XML but **do not post to real boards** (Google adapter is an explicit stub). "Syndication works" = feed generation only.
- **AI Notice content** — the 5 compliance tests assert a **hardcoded string defined inside the test file**, not the shipped notice.
- "1028 total tests" (WHAT_CHANGED.md) — a cross-repo sum; the real per-repo verified numbers are in the table above (217 / 238 / 782 / 51 / 328 / 0).

**execo-bridge — three screens that DEMO GREAT but are FICTION against the real backend:**
- **Matching board (7-dimension scores)** — frontend calls `GET /matches`; backend only has `POST /matches`. **404s in prod**, populated only by demo mock.
- **Interview Kits** — calls `GET /jobs/<id>/interview-kit`; **no such backend route exists.** Demo-only.
- **Skills / verification system** — calls `GET /candidates/<id>/skills`; **no such backend route exists.** Demo-only.
- **Screening summaries + candidate names** in Screening Review / Candidate Profile — demo-only enrichment layered over a real feed; degrades against real backend.
- **Entire frontend** — **zero automated tests.** Every "it works" is a manual click-through.

**governance-platform:**
- **LLM-path PII sanitizer** — the sanitizer that actually runs on LLM logs (`_sanitize_for_log`) is a **separate, weaker, untested** function that does NOT strip API keys (the tested one does).
- **Snapshot input validation** (`_validate_answers`), **scoring-report / gap-analysis JSON parsing**, **compliance chat streaming** — real code, **no tests.**
- **RAG, PDF generator, email, Stripe billing/webhooks, auth/JWT, assessments** — present in code, **zero test coverage.**

**hr-advisory-agent:**
- **Cara live voice/LLM conversation quality**, **ChromaDB per-company RAG retrieval**, **live Stripe payment capture**, **Gmail SMTP delivery**, **the 24-agent fleet's actual runtime behavior**, **frontend + Capacitor mobile** — all either mocked at the edge or untested. Resilience is tested; outcomes are not.

---

## Corrections to prior self-reports (stop repeating these)

1. **"Smoke-bypass prod guard is part of the rebuild"** (execflex WHAT_CHANGED.md L98) — **FALSE.** It's on the unmerged `security-hardening` branch. Prod is unprotected.
2. **"agentic-core 736 tests"** (WHAT_CHANGED.md) — real number is **782**.
3. **"transparency 24 DFY-pack tests"** (WHAT_CHANGED.md) — real number is **238** total; the list-numbering fix is on master, and there is **no `defect-fixes` branch**.
4. **"1028 total tests"** — not a meaningful figure; use per-repo numbers.
5. **ExecFlex AI features are demoable** — **FALSE in prod** (missing `agentic_core` + `anthropic` in requirements) and **untested everywhere** (no real-LLM test).
6. **agentic-core v0.17.0 is in production** — no; the prod pin is v0.15.0 / v0.16.4 (docs disagree), not 0.17.

## Highest-value fixes to convert flags into LIVE

1. **Add `agentic_core` + `anthropic` to execflex-backend `requirements.txt`**, redeploy, verify AI endpoints return real output. (Unblocks the entire ExecFlex AI story.)
2. **Merge `security-hardening` to execflex main** and redeploy — closes the prod auth-bypass hole.
3. **Ship the 3 missing ExecFlex backend routes** (`GET /matches`, `/jobs/<id>/interview-kit`, `/candidates/<id>/skills`) so the matching board, interview kits, and skills screens stop being demo-only fiction.
4. **Add at least one real-LLM smoke test per product** (gated, opt-in) so "the AI works" has evidence.
5. **Push hr-advisory-agent local main to origin** (8 commits ahead) so the remote reflects reality.
6. **Add a runtime cross-org isolation test** to execflex-backend to back the multi-tenant claim behaviorally.
