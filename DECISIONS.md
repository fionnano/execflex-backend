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

## D-8: Console contract fixes shipped with the port
Three real-backend contract mismatches the Aidan flow would trip over were fixed
server-side: pipeline board now returns `{stages:[...]}` (was a stage-keyed dict
the board couldn't render), `GET /compliance/decisions?type=` prefix-matches
decision families (screening → screening_score), and candidates are serialized
to the console shape. Client-side, ScreeningDetail no longer crashes on real
outcome objects, and ScreeningReview builds its name/session maps from live
data instead of demo fixtures.

## D-9: Add Candidate intake added
No console surface called `candidatesApi.create` — a brand-new org had no way
to get a candidate in front of Aidan. Added a minimal Add Candidate dialog
(dashboard + pipeline board). Bulk import remains on the roadmap (FEATURE_GAP).

## D-10: security-hardening branch — already merged
The audit flagged it as parked; `git log main..security-hardening` is empty, so
the prod smoke-bypass guard is on main. No action needed.

## D-11: index.html cleanup
Rebranded head to ainm Search and removed the leftover `gptengineer.js`
(Lovable dev tool) from the production page.

## D-12: Org provisioning verified empirically
A repo sweep finds no hook code (it is configured in the Supabase dashboard,
not in git), but a live test proves it: a brand-new user created via the admin
API logs in with `app_metadata = {org_id, role: "owner"}` in the JWT, and the
org-scoped v1 endpoints accept it. The "new user cannot use the console"
concern from the code-only audit is disproven in prod.

## D-13: Live verification method
Created a synthetic prod user (fionnano+aidan-console-selftest@gmail.com) via
the Supabase admin API, logged in with password grant, created a synthetic
candidate ("Aidan Selftest") whose phone is the owner's own number (the one
that received tonight's proven old-app call), and started a real Aidan call
through POST /api/v1/screens/phone on the deployed backend. No real candidate
data used; the only phone dialled is the owner's.

## D-14: Marketplace MVP storage — reuse existing durable tables (no new prod DDL)
There is no autonomous path to apply DDL to the execflex prod Supabase
(krzacydualjpsapffpfm): no DB password / pooler string in any repo, no
`exec_sql`-style RPC, no management token. Prior migrations (rebuild-v1) were
applied by a human via the Supabase dashboard SQL editor. Rather than block the
run, the /marketplace MVP persists on the existing durable, org-scoped tables,
namespaced so it never collides with the /console recruiter product:
  - **Leaders** → `people_profiles`, `organization_id = MARKETPLACE_ORG_ID`
    (a single dedicated "ainm Marketplace" org), `source='marketplace_leader'`;
    seniority/sectors/engagement/comp/vetting_status/vetting_score/rationale live
    in `source_metadata` (JSONB). The pool is read GLOBALLY (a curated public
    catalog by design) — never org-filtered — so it appears in /marketplace but
    in no recruiter's console (which filters people_profiles by the caller's own
    org_id, never the marketplace org).
  - **Companies + Opportunities** → `opportunities`, `metadata.marketplace=true`,
    company captured in `metadata.company`.
  - **Introductions (billable event)** → `activity_log`, `entity_type='placement'`,
    `metadata.marketplace=true` + the full intro record (leader/company/opportunity,
    status, placement_fee_pct, placement_fee_amount, first_year_comp, hired).
    Owned by the requesting company's org_id.
  - **Vetting audit** → `ai_decision_log`, `decision_type='screening_score'`,
    `model_used='marketplace_vetting_v1'`.
This reuses the estate (an explicit goal) and is fully durable tonight with zero
human step. A clean dedicated-tables migration
(`supabase/migrations/20260714_marketplace.sql`) is committed as the graduation
path; it is idempotent and ready but NOT yet applied to prod (needs a human to
paste it into the dashboard). Flagged in SHIPPED.md.

## D-15: Marketplace is a separate surface, org-auth reused
/marketplace (frontend) + /api/v1/marketplace (backend) are a NEW product surface.
They reuse the console's org-scoped JWT auth (`require_org`) but do NOT modify
ainm Search /console, ainm.ai, transparency, or governance. The leader pool is a
shared curated catalog; the demand side (browse + request intro) and the admin
introductions pipeline are the two sides plus the operator view.

## D-16: Vetting engine — agentic-core with deterministic fallback
Vetting scores a structured technical+leadership response set via agentic-core's
AnthropicClient: per-answer mechanical scoring routed to Haiku
(claude-haiku-4-5), the overall explainable rationale + pass/fail routed to
Sonnet (claude-sonnet-4-5), matching the ModelRouter policy (EXTRACTION→Haiku,
REASONING→Sonnet). When the AI flag is off or the LLM is unavailable, a
deterministic heuristic scorer produces a score + rationale + pass/fail so the
demo and tests never depend on live tokens. Pass threshold = 70/100 →
vetting_status='verified', badge "Independently vetted" (no "top X%" claim);
methodology in VETTING_METHODOLOGY.md. This is a structured assessment, not a
live proctored coding test — the assessment-adapter seam is preserved so a real
proctored tool can plug in later.
