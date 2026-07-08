# FEATURE_GAP.md — OLD ExecFlex vs NEW ainm Search console

**Generated:** 2026-07-08
**Method:** Three read-only audits — old frontend inventory, new `/console/*` inventory, and an end-to-end Aidan/voice trace across frontend (execo-bridge) + backend (execflex-backend). No code changed.
**Goal this feeds:** one merged product in the ainm look, every working feature carried forward, no old UI left behind.

## The shape of the gap (read this first)

The two apps are **different products that overlap in the middle**:

- **OLD ExecFlex** = a **two-sided executive-search marketplace** with a **voice-phone screening funnel** ("Aidan"), candidate-facing pages, public shareable artifacts, retainer/placement-fee economics, and an outbound sourcing/BD CRM. Built on Supabase-direct + the legacy Flask client (`@/lib/api.ts`). Per ESTATE_STATUS.md the whole execo-bridge repo is **undeployed, untested**; "active" = wired to a real backend in code.
- **NEW /console** = an **internal agency ATS** (jobs → pipeline → matching → screening-review → compliance → talent-pools) in the dark "ai•nm Search" shell, on the modern `@/lib/api-v1.ts` client. **No old page uses api-v1; no console page uses the legacy client** — the two halves are cleanly disjoint.

So "merge" = **(a)** carry the agency-internal features that are better in new (done), **(b)** port the marketplace/candidate/voice/economics features that only exist in old, and **(c)** drop the genuinely dead pages.

**Tag key:** `PRESENT-IN-NEW` (covered, usually better) · `MISSING-FROM-NEW` (no console equivalent) · `BETTER-IN-OLD` (exists in new but old does more) · `DROP` (dead/placeholder).
**Effort key:** S = <1 day (restyle/move existing) · M = 1–3 days (new console page, backend exists) · L = 1–2 wks (new FE + some BE) · XL = 2+ wks (new BE integration + FE).

---

## 🎙️ PRIORITY: Aidan / voice screening

**Where it lives in OLD:** `CallAiDanModal.tsx` + `VoiceInterface.tsx` (route `/voice-interface`) + `CallReview.tsx` (`/call-review`), driven by the `useScreeningCall.ts` hook. Launched from ~11 old surfaces (`RoleDetail` "I'm interested", `CandidateLanding`, `Dashboard`, `CandidateDashboard`, nav menus, `ExecutiveActions`, `ProfileActions`, `ApiMatchCard`, `ExecutiveCard`).

**Transport:** **outbound Twilio phone call** — the candidate literally gets phoned. `POST /screening` → `routes/voice.py` (TwiML/webhooks) → `routes/voice_websocket.py` (Twilio Media Streams ↔ OpenAI Realtime + ElevenLabs); results land in Supabase `outbound_call_jobs` + `interactions`, reviewed in `/call-review`.

**Is it wired into `/console`? — NO. Definitively zero.** Grep of `src/pages/console/*` + `src/components/console/*` for every voice/call/websocket/mic/twilio/cara/aidan token returns nothing. Console screening (`ScreeningReview`, `ScreeningDetail`) only **reviews already-completed decisions** via the text `/api/v1/screens` state machine; `screeningApi.create/answer/score` exist in the client but **no console page calls them** — there is no "start screening" button anywhere.

**Two separate voice systems (don't conflate):**
| | **AI Dan** (old) | **Cara** (new, actively developed) |
|---|---|---|
| Transport | Twilio outbound **phone call** | **browser mic** → WebSocket (PCM16 24kHz) |
| Backend | `/screening`, `voice.py`, `voice_websocket.py`, `voice_calls.py` | `cara_voice.py` (`POST /voice-session/cara`) + `cara_websocket.py` (`/voice/cara/ws/:id`) → OpenAI `gpt-realtime` |
| Consumed by | execo-bridge (old pages) | the **ainm.ai HR frontend**, NOT execo-bridge |
| Screening scoring | yes — feeds `outbound_call_jobs` + extraction/scoring | no — returns raw transcript turns only |
| Recent commits | stable/legacy | all 5 latest voice commits are Cara |

**MISSING-FROM-NEW — this is the single biggest gap.** Port options:

- **Fastest (M–L): reuse the AI Dan phone flow.** The backend + `useScreeningCall` + `CallReview` already work end-to-end. Port = surface `CallAiDanModal` (restyled) as a "Screen by phone" action on `CandidateProfile`/`PipelineBoard`, and bring `CallReview` into the console shell. No new backend. Caveat: depends on the Twilio account (the same one throwing 20003 on SMS — verify voice works).
- **Best/modern (XL): Cara browser voice in-console.** Reuse `POST /voice-session/cara` + `/voice/cara/ws/:id` (works today), but build the missing frontend (mic capture + PCM16 encoder + WS client speaking Cara's `{type:"audio"}`/`transcript_*`/`call_ended` protocol — none exists in execo-bridge) **and** wire Cara's transcript turns into `/api/v1/screens/:id/answer` + `/score`. The core missing integration is that Cara (transcript-only) and `/screens` (scoring, text-only) are disjoint pipelines.

**Recommendation:** ship the phone flow into the console first (M–L, reuses everything), then invest in Cara-in-console (XL) as the modern replacement.

---

## Feature matrix — every OLD feature

### Client / hiring side
| OLD feature | Tag | Notes / port | Effort |
|---|---|---|---|
| **PostRole** `/post-role` | `PRESENT-IN-NEW` | `console/JobForm` is strictly better (mandatory pay-range, AI JD gen, bias check, syndication). | — |
| **Dashboard** (old employer hub) — matched candidates, screening results | `BETTER-IN-OLD`→split | Core covered by console Dashboard + MatchingBoard (better: 7-dim, LLM rerank). But old also has **retainer payment + sourcing panel** (below). | — |
| ↳ **€1,500 retainer Stripe payment** | `MISSING-FROM-NEW` | `payRetainer` (Stripe) exists; add a billing/checkout surface to console. | M |
| ↳ **Admin sourcing panel** (enrich, outreach, approve sourced) | `MISSING-FROM-NEW` | Flask `/roles/*/enrich-candidates`, `send-outreach`, `approve-sourced` exist; needs console UI. | M |
| **MatchDetail** `/match/:id` (candidate views match, accepts intro) | `MISSING-FROM-NEW` | Candidate-side + **intro-request** workflow; console matching is agency-internal ranking only. | M |

### Voice / screening
| OLD feature | Tag | Notes / port | Effort |
|---|---|---|---|
| **AI Dan voice phone screening** (`/voice-interface`, CallAiDanModal, CallReview) | `MISSING-FROM-NEW` | See priority section. Reuse phone flow (M–L) or Cara browser (XL). | M–XL |
| **Screening** `/screening` (admin live AI-call dashboard + replay) | `BETTER-IN-OLD` | New `ScreeningReview` reviews text decisions; old shows **live call status + replay** of `outbound_call_jobs`. Restyle into console. | M |
| **FairScreening** `/fair-screening` (public bias/fairness policy) | `MISSING-FROM-NEW` | Candidate-facing EU AI Act/GDPR page; console Compliance is agency-internal. Mostly static + `/screening/bias-policy`. | S |
| **MyScreening** `/my-screening` (candidate result + right-to-explanation) | `MISSING-FROM-NEW` | Token-gated candidate portal; `/screening/candidate-status`, `/feedback-request` exist. | M |

### Public marketplace & shareable artifacts
| OLD feature | Tag | Notes / port | Effort |
|---|---|---|---|
| **FindRoles** `/find-jobs` + **PostedRoles** `/posted-roles` (public job board) | `MISSING-FROM-NEW` | Console `JobsList` is internal agency management, not a public candidate-facing board. Supabase `opportunities` / v1 jobs (public read) exist. | M |
| **FindTalent** `/find-talent` (public talent search) | `MISSING-FROM-NEW` | Legacy Flask `/match` exists; console Matching is job-scoped internal. | M |
| **RoleDetail** `/role/:id` (public role + "I'm interested"→voice) | `MISSING-FROM-NEW` | Public detail page; ties into the voice intake funnel. | M |
| **Shortlist** `/shortlist/:id` (public shareable scored shortlist + request intro) | `MISSING-FROM-NEW` | Client opens a branded scored shortlist without login; legacy `/shortlist/{id}` exists. High-value sales artifact; no console analog. | M |

### Candidate / executive side
| OLD feature | Tag | Notes / port | Effort |
|---|---|---|---|
| **ExecutiveOnboarding** `/my-profile` (10-step wizard + **DEI capture** + AI summary) | `MISSING-FROM-NEW` | Candidate self-onboarding + diversity self-declaration; no console equivalent (console adds candidates internally). | L |
| **CandidateDashboard** `/candidate-dashboard` (candidate hub: matches, screening gate, intros) | `MISSING-FROM-NEW` | Whole candidate-facing audience the console doesn't serve. | L |
| **MyPublicProfile** `/my-public-profile` | `MISSING-FROM-NEW` | Candidate public profile preview. | S–M |
| **ProfileCompletion** `/profile-completion` | `MISSING-FROM-NEW` | Part of candidate onboarding funnel. | S |
| **LinkedInConnect** `/linkedin-connect` (LinkedIn OAuth import) | `MISSING-FROM-NEW` | OAuth exists in legacy client; useful for candidate enrichment. | M |
| **Member** `/member` (post-auth candidate hub) | `MISSING-FROM-NEW` | Minimal; low value — fold into candidate dashboard. | S |
| **ChooseMode** `/choose-mode` (talent vs hiring) | `DROP`(N/A) | Agency console is single-audience; mode-switch not needed. | — |

### NED (board-director) vertical
| OLD feature | Tag | Notes / port | Effort |
|---|---|---|---|
| **NEDOnboarding** `/ned-onboarding` (board/governance profile) | `MISSING-FROM-NEW` | A generic ATS has no board-director schema; niche but differentiating. | L |
| **NEDTraining** `/ned-training` | `DROP` | Hardcoded text, no engine — dead placeholder. | — |

### Billing & admin / ops
| OLD feature | Tag | Notes / port | Effort |
|---|---|---|---|
| **Billing** `/billing` (plan/usage/Stripe) | `MISSING-FROM-NEW` | No billing surface in console; `useBilling` + Stripe exist. | M |
| **Admin** `/admin` (users, transcripts, roles, set-admin) | `MISSING-FROM-NEW` | Ops/user management; legacy `/onboarding/*`. | M |
| **admin/Candidates** (bulk CSV upload, pool, enqueue calls) | `MISSING-FROM-NEW` | Console has candidates but **no bulk import / enqueue-call**; endpoints exist. | M |
| **admin/Upload** (CSV/XLSX import hub + DB stats) | `MISSING-FROM-NEW` | Bulk data import; `/admin/upload/*` exists. | M |
| **admin/Clients** (outreach CRM, bulk email campaigns, funnel) | `MISSING-FROM-NEW` | Outbound BD CRM — distinct from talent-pools; `/admin/clients/*` exists. | L |
| **admin/Revenue** (placement fees, retainers, funnel) | `MISSING-FROM-NEW` | Executive-search economics (retainer + 15% placement); `/admin/revenue` exists. | M |

### Marketing / landing (public funnel — keep, restyle to ainm)
| OLD feature | Tag | Notes | Effort |
|---|---|---|---|
| **Landing** `/`, **CandidateLanding** `/join`, **ClientLanding** `/hire`, **Pricing**, **Resources** | `PRESENT-IN-NEW`(shell) | Already "ainm Search" branded; keep as the public funnel. Ensure "Talk to Aidan" CTAs point at the ported voice flow. | S each |
| **Executives** `/executives` | `DROP` | Static marketing superseded by newer landings. | — |
| **ExecutiveProfile** `/executive-profile` | `DROP` | No backend; only reachable via nav-state. | — |

---

## What's already carried forward (PRESENT-IN-NEW, better)

Jobs (JobForm > PostRole), Matching (7-dimension + LLM rerank > legacy `/match`), Pipeline (Kanban — new), Compliance Centre (internal — new), Talent Pools (new), Interview Kits (new), Screening **review** (structured decision review > old). The agency-internal ATP spine is done and better; the gaps are almost entirely **candidate-facing, public-marketplace, voice, and exec-search-economics** surfaces.

## Drop list (don't port)
`NEDTraining` (dead), `ExecutiveProfile` (no backend), `Executives` (static), `ChooseMode` (single-audience console). Old marketing landings: keep but restyle.

## ⚠️ Cross-cutting caveats that affect porting
1. **Demo mode only mocks GET.** Every console POST/PATCH already hits the live backend — so ported write-features need real routes, and three console reads are still demo-only fictions per ESTATE_STATUS.md (`GET /matches`, `/jobs/:id/interview-kit`, `/candidates/:id/skills` don't exist server-side). Fix those before/with any port.
2. **Client split.** Old pages use `@/lib/api.ts` (legacy Flask: `postRole`, `requestIntro`, `findMatches`, LinkedIn, shortlist) + Supabase-direct; console uses `@/lib/api-v1.ts`. Ported features should migrate onto api-v1 or the legacy endpoints must stay alive.
3. **Voice depends on Twilio health** (phone path) — the same Twilio account had the 20003 SMS failure; verify the voice product/number before promising the phone flow.

## Recommended merge sequence (shortest path to "one product, ainm look, nothing left behind")
1. **Voice into console (phone flow first)** — highest value, reuses the working AI Dan backend + `useScreeningCall` + `CallReview`; surface as a console action. *(M–L)*
2. **Public artifacts that sell** — `Shortlist/:id` + public job board + role detail, restyled. *(M each)*
3. **Exec-search economics** — retainer payment + `admin/Revenue` placement funnel + `Billing`. *(M each)*
4. **Candidate-facing layer** — `ExecutiveOnboarding` (+DEI), `CandidateDashboard`, `MyScreening`/`FairScreening` transparency portal. *(L)*
5. **Ops** — bulk upload/enqueue, admin user mgmt, client outreach CRM. *(M–L)*
6. **Cara browser voice in-console** as the modern replacement for the phone flow. *(XL)*
7. **Delete** the drop-list routes and any old UI once its console equivalent ships.
