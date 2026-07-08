# SHIPPED.md — ainm Search recruiter product (run of 2026-07-08)

Autonomous run: make /console the product, port Aidan into it, deploy, self-verify.
All decisions in DECISIONS.md. Everything below was verified against the live
deployments, not just local code.

## What's live right now

**Frontend — https://execflex.ai (Hetzner, bundle `index-aH77Vj1l.js`)**
- Live bundle hash matches the local build; HTTP 200; deployed via deploy.sh
  (.env.local-aside pattern). execo-bridge `main` = `2799eac`, pushed.
- Post-login/signup landing is **/console** (verified in the deployed JS:
  `decodeURIComponent(a):"/console"`). Old ExecFlex routes remain reachable by
  direct URL only; console pages render only the AgencyLayout nav (no old shell).
- `<head>` rebranded to **ainm Search** (title/meta/OG/Twitter/JSON-LD verified
  live); leftover `gptengineer.js` dev script removed.
- Deployed bundle contains the Aidan-in-console code ("Call with Aidan",
  "Screen with Aidan", "Add Candidate", `screens/phone`, `console/screening-review`).

**Backend — https://execflex-backend-1.onrender.com (Render, commit `f416480`)**
- execflex-backend `main` pushed; Render auto-deployed; `/health/runtime`
  confirms the live commit. 241 tests green (was 224; +17 new real-path tests).

## What was ported / built

**Aidan voice screening in the console (the FEATURE_GAP.md priority):**
- `POST /api/v1/screens/phone` — org-scoped start of the proven AI Dan call path
  (`create_screening_job` → dispatcher → Twilio → OpenAI Realtime; call machinery
  untouched), linked to a `screening_sessions` row (`metadata.outbound_call_job_id`).
- `GET /api/v1/screens/:id/call-status` + `GET /api/v1/screens?candidate_id=` —
  org-scoped polling/listing with **read-through sync**: completed call results
  (transcript, scores ×2 to /10, recommendation, extraction) are copied into the
  console session and an `ai_decision_log` row is written for the review queue.
- Console UI: `AidanCallModal` (port of CallAiDanModal — zero old-route links;
  completion lands on `/console/screening-review/:sessionId`), `useAidanCall`
  hook (same proven polling state machine), "Call with Aidan" on the candidate
  profile, sessions listed on the profile's Screening tab, outcome + full call
  transcript rendered on ScreeningDetail.
- Auth reconciled: the whole flow runs on the console's org-scoped JWT
  (`app_metadata.org_id`); quota + rate-limit enforced server-side (free tier =
  1 screening/month, so a brand-new user's first call works).

**Real-backend fixes the flow would have tripped over (all were demo-only fictions):**
- Candidates API now serializes the console shape (`full_name`, `email`, `phone`
  from `source_metadata`, `experience_years`, `skills`) and accepts those fields
  on create/update.
- Pipeline board returns `{stages:[...]}` (was an unrenderable dict).
- `compliance/decisions?type=` prefix-matches families (screening → screening_score).
- ScreeningDetail no longer crashes on real outcome objects / state-machine answers.
- ScreeningReview resolves candidate names + session links from live data.
- **Add Candidate** dialog (dashboard + pipeline board) — previously no console
  surface could create a candidate at all.

## Aidan verification result — REAL CALL, CONSOLE PATH, PASSED

Performed 2026-07-08 ~01:15 UTC against production, exercising the exact
brand-new-user journey:

1. Synthetic user created via Supabase admin API → password login → JWT carried
   `app_metadata {org_id: 6f5f93c1…, role: owner}` — **org provisioning hook
   confirmed live** (it is dashboard-configured; not in git — a code-only audit
   will wrongly conclude it doesn't exist).
2. `POST /api/v1/candidates` → synthetic candidate "Aidan Selftest"
   (`7f4630ff…`), phone = the owner's own number (the one that received the
   proven old-app call at 23:08).
3. `POST /api/v1/screens/phone` → job `6a49e1e1…`, session `1bdc7576…`, queued.
4. Live status via the org-scoped endpoint: `queued → ringing → completed`,
   `extraction complete` (~60s). **A real Twilio call was placed and answered by
   the owner's voicemail** (it was ~1 AM; the greeting is in the transcript).
5. Read-through sync verified: session `state=complete`, `completed_at` set,
   outcome `hold` (fair for a voicemail), transcript + extraction stored in
   metadata, session listed under the candidate. Decision row logged.
6. Synthetic auth user deleted afterwards; DB rows left for audit.

Caveat: because the call hit voicemail, the *conversation quality* path
(questions asked, per-question scores) was not exercised live — that needs a
human answering (see below). The transport, dispatch, Twilio bridge, OpenAI
extraction, org auth, and console data bridge are all proven live end-to-end.

## Still needs a human — ranked

1. **Answer one Aidan call from the console** (daytime): open a candidate with
   your own phone → "Call with Aidan" → answer → complete the interview →
   confirm per-question scores render in /console/screening-review/:id.
   (Everything up to "human answers" is verified.)
2. **Magic-link email UX check**: sign up on execflex.ai with a fresh inbox and
   click through — the code path is live and redirect verified, but actual email
   deliverability/spam-folder behaviour wasn't testable autonomously.
3. **Transcript encoding**: tonight's transcripts contain mojibake
   ("Se�n � Sullivan") written by the voice pipeline — pre-existing (matches the
   encoding work in recent diag commits), lives in the Twilio/OpenAI transcript
   writer, not the new bridge.
4. **Old-route retirement decision**: old ExecFlex pages are off the default
   path but still route by URL. Decide per FEATURE_GAP.md what to port
   (shortlists, billing) vs delete.
5. **Stripe subscription surface**: quota enforcement is live (free tier = 1
   screening/month, 403 + upgrade message beyond it), but the console has no
   billing/upgrade page yet — FEATURE_GAP.md "exec-search economics" tier.
6. **Console demo-data residue**: DEMO_* summaries still overlay in dev demo
   mode only; prod builds have it hard-blocked (vite guard). No action needed,
   just awareness.

## Repo state

| Repo | Branch | HEAD | Pushed | Deployed |
|---|---|---|---|---|
| execflex-backend | main | `f416480` + docs | yes | Render (verified `/health/runtime`) |
| execo-bridge | main | `2799eac` | yes | execflex.ai (bundle hash verified) |
