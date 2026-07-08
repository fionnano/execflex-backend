# DECISIONS.md — ainm Search ship run (2026-07-08)

Autonomous run: finish ainm Search as a shippable recruiter product (console default,
Aidan ported into console, deployed to execflex.ai). Decisions logged here as made.

## D-1: Backend branch strategy
Work on `aidan-console-bridge` branched from `cleanup-ai-debug-scaffolding` (which is
main + 1 cleanup commit that was clearly intended to merge). Merge to `main` and push
at ship time (Render deploys main).

## D-2: Aidan port shape — single org-scoped endpoint over the proven service
`POST /api/v1/screens/phone` (org-scoped, owner/recruiter) calls the existing
`services/screening_service.create_screening_job()` — the exact service the proven
old-app path uses. The Twilio/OpenAI call machinery (dispatcher worker, voice.py,
voice_websocket.py) is untouched. This is "wire the proven path in", not a rebuild:
the legacy route handler is a thin wrapper over this same service.

## D-3: Data bridge = linking metadata + read-through sync (no schema migration)
`screening_sessions.metadata` (existing JSONB) stores `outbound_call_job_id` +
`interaction_id` + `channel: "aidan_phone"`. When the console reads a session
(GET /screens/:id, GET /screens list, or the new phone-status endpoint) and the
linked call has completed, the backend syncs call results into the session row
(state=complete, answers from screening_scores, outcome from recommendation).
Rationale: no prod DB migration needed (can't safely run DDL against prod Supabase
from this run), works retroactively on every read, and keeps `screening_sessions`
(the console's source of truth) authoritative.

## D-4: Score scale mapping
Legacy Aidan scores are 1–5 per question; the console ScreeningDetail renders /10.
Bridged answers store score×2 (1–5 → 2–10). Outcome/decision-log score is stored
0–1 (avg/5) to match how the console renders compliance decisions (score×100 %).

## D-5: Candidate serializer fixes demo-fiction fields server-side
Console expects `full_name`, `email`, `phone`, `experience_years`, `skills` on
candidates; `people_profiles` has `first_name/last_name`, `years_experience`, and
contact only in `source_metadata.upload_email/upload_phone`. Fixed in the v1
candidates API with a serializer (and create/update accept the console field names,
storing contact into `source_metadata`). Chosen over frontend adaptation so every
consumer gets the correct shape.

## D-6: Billing gate moved server-side for the console
The old modal used the legacy per-user `useCanPerformAction("view_screening")` hook.
The console modal drops that frontend gate; `POST /api/v1/screens/phone` enforces the
same `check_quota(user_id, "screenings_done")` + rate-limit server-side and returns a
403 with an upgrade message the modal displays. Free tier = 1 screening/month, so a
brand-new user's first Aidan call works.

## D-7: Verification approach
Live-cred Twilio call from the console path will be attempted only if creds are in
this repo's .env AND the dispatcher can run; otherwise ship with a real-path test of
the new endpoints (transport stubbed at the create_screening_job/Twilio boundary)
plus honest logging in SHIPPED.md that a live-cred prod verification remains.
