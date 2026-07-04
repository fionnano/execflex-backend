# Operational Findings — Dead Code, Active Costs, and Running Services

Audit date: 2026-07-04

---

## Active Services That May Be Billing

### Confirmed Active (from code and deployment config)

| Service | Evidence | Estimated Monthly Cost | Notes |
|---------|----------|----------------------|-------|
| **Render.com** (backend) | Procfile present, hardcoded `execflex-backend-1.onrender.com` | $7-25/mo (starter/standard) | Single web service, gunicorn |
| **Supabase** | Project `krzacydualjpsapffpfm`, required for all operations | $0-25/mo (free/pro) | Database + Auth + RLS |
| **Twilio** | Active integration in voice_websocket.py, voice.py | Pay-per-use (~$0.02/min outbound) | Only charges on active calls |
| **OpenAI** | Realtime API (voice) + GPT-4 (extraction, scoring) | Pay-per-use (~$0.06/min Realtime) | Charges on voice sessions + text completions |
| **ElevenLabs** | Voice ID configured, TTS in voice calls | Pay-per-use or $5-22/mo plan | Character-based billing |
| **Stripe** | Live publishable key (`pk_live_*`) in frontend | 2.9% + $0.30 per transaction | Only charges on transactions |
| **People Data Labs** | `PDL_API_KEY` env var | Free tier: 100/month | Charges above free tier |
| **PostHog** | EU instance configured | Free tier: 1M events/mo | Likely within free tier |

### Possibly Active (not confirmed from code alone)

| Service | Evidence | Risk |
|---------|----------|------|
| **Frontend hosting** (Netlify/Vercel/other) | Not visible in backend repo; frontend must be hosted somewhere | Could be on free tier or $0-20/mo |
| **Gmail SMTP** | `EMAIL_USER` + `EMAIL_PASSWORD` env vars | No cost, but app passwords could be active |
| **LinkedIn OAuth app** | Client ID/secret configured | No cost, but app registration is active |
| **Domain registrations** | `execflex.ai`, `ainm.ai`, `www.ainm.ai` referenced in CORS | Renewal costs (~$12-50/yr each) |

### Confirmed Disabled

| Service | Evidence |
|---------|----------|
| **Apollo.io** | Code comment says "requires paid plan"; sourcing functions exist but are gated/disabled |

---

## Background Processes and Scheduled Tasks

These run continuously on the Render backend and consume resources even when the platform is dormant:

| Process | Interval | Purpose | Cost Implication |
|---------|----------|---------|------------------|
| Voice monitor probe | Every 5 minutes | Synthetic Cara voice test (POST session + WS connect + OpenAI handshake + audio check) | Each probe consumes ~5-10s of OpenAI Realtime API time. At 288 probes/day, this could be $5-15/month in OpenAI costs even with zero real users. |
| Session cleanup thread | Every 120 seconds | Removes expired `/tmp/cara_sessions/` files | Negligible CPU cost |
| Call dispatcher | On-demand (HTTP trigger) | Polls `outbound_call_jobs` for queued calls | Only runs when triggered; no standing cost |

**Decision D-011**: The voice monitor probe is actively consuming OpenAI Realtime API credits continuously. If the platform is dormant, this is pure waste.

---

## Dead / Abandoned Code

### Clearly Dead

| Component | Location | Evidence | Size |
|-----------|----------|----------|------|
| Apollo.io sourcing | `services/apollo_service.py` | Comment: "requires paid plan"; functions exist but service is disabled | ~200 lines |
| Inbound call handler | `routes/voice.py` — `POST /voice/inbound` | Stub function, never implemented | ~10 lines |
| Legacy speech capture | `POST /voice/capture` | Marked as deprecated in OpenAPI spec | ~30 lines |
| Demo seeder | `routes/seed.py` | Moorepark meeting demo data; still registered but purpose has passed | ~400 lines |
| Debug endpoints | `routes/voice.py:479-520` | Development debugging left in production | ~40 lines |

### Potentially Dead (requires usage data to confirm)

| Component | Location | Evidence | Size |
|-----------|----------|----------|------|
| AI Consultant chat | `routes/ai_consultant.py` | Full implementation exists but unclear if any frontend page uses it actively | ~13KB |
| Talent network routes | `routes/talent_network.py` | Public opt-in endpoint; unclear if the landing page is live | ~18KB |
| LinkedIn OAuth | `services/linkedin_service.py`, `routes/onboarding.py` | Full OAuth flow implemented; unclear if users are actively connecting | ~15KB |
| Bias audit/policy endpoints | `routes/screening.py` (3 endpoints) | EU AI Act compliance; may be required for legal reasons even if rarely accessed | ~5KB |
| Landing page lead capture | `routes/health.py` — `POST /submit-brief` | Feeds `inbound_leads` table; depends on whether static landing page is live | ~2KB |
| Client outreach management | `routes/clients.py` | Full CRUD for client contacts; unclear if admin uses it | ~12KB |

### Root SQL Files (execo-bridge)

22 `.sql` files in the frontend repo root are manual admin scripts, not automated migrations. These appear to be one-time operations that have already been executed:

- `ADD_ADMINS_MANUAL.sql` — one-time admin role grants
- `SEED_IRISH_EXECUTIVES_EUR.sql` — demo data seeding (6 profiles)
- `DELETE_USER_*.sql` — one-time user cleanup
- `APPLY_USER_PREFERENCES_NOW.sql` — one-time preference migration
- Various `CHECK_*`, `VERIFY_*`, `DEBUG_*` — diagnostic queries

These files should be archived or moved to a `scripts/` directory.

---

## Code Size and Complexity

| File | Size | Complexity Notes |
|------|------|-----------------|
| `routes/voice_websocket.py` | ~130KB | Largest file. Full Twilio-OpenAI bridge with VAD, overlap guards, state machine, fallback TTS. Difficult to maintain. |
| `routes/billing.py` | ~75KB | 30+ endpoints. Combines Stripe billing, placement tracking, bulk candidate operations, enrichment, outreach — should be split. |
| `routes/upload.py` | ~32KB | Flexible multi-format upload with header mapping |
| `routes/seed.py` | ~21KB | Demo data seeder — potentially removable |
| `routes/onboarding.py` | ~20KB | Admin operations, LinkedIn OAuth, user management |
| `routes/cara_websocket.py` | ~19KB | Cara voice bridge (includes diagnostic logging from recent debugging) |
| `routes/talent_network.py` | ~18KB | Talent network opt-in |
| `routes/shortlist.py` | ~16KB | Shareable shortlists |
| `modules/email_sender.py` | ~28KB | SMTP email sending (4 email types) |

---

## Dependency Status

68 packages in `requirements.txt`. Key observations:

- **Flask 3.1.0** — current
- **cryptography 44.0.0** — current
- **PyJWT 2.10.1** — current
- **stripe 11.4.1** — current
- **twilio 9.3.1** — current
- **supabase 2.x** — current
- **No known CVEs** flagged from version inspection (a proper `pip audit` should be run for certainty)

Frontend (`execo-bridge`):
- **React 18.3.1** — current
- **Vite 5.4.1** — current (5.x line)
- **@supabase/supabase-js 2.48.1** — current
- **TypeScript 5.5.3** — current
- **posthog-js 1.365.0** — current

---

## Render Deployment Notes

- Single web service, single worker (WebSocket constraint)
- No separate worker dyno — call dispatcher runs on-demand via HTTP
- Background threads (monitor, cleanup) run in-process
- No CI/CD pipeline visible in repo (no `.github/workflows`, no `render.yaml` build commands beyond Procfile)
- Deploy fingerprint logging (`[DEPLOY] commit=<hash>`) confirms which commit is running

---

## Cost Estimate: Dormant Platform

If the platform is running but has zero active users:

| Item | Monthly Cost |
|------|-------------|
| Render (backend) | $7-25 |
| Supabase (free or pro) | $0-25 |
| OpenAI (voice monitor probes only) | $5-15 |
| Domain renewals (amortized) | $2-8 |
| **Total standing cost** | **$14-73/mo** |

The voice monitor is the only component actively consuming paid API resources while dormant. All other services are idle or on free tiers.

---

## Diagnostic Code to Remove Before Any Reactivation

These items were added during recent debugging sessions and should be cleaned up:

| File | What | Added in |
|------|------|----------|
| `routes/cara_websocket.py` | Catch-all OAI_EVENT logging, PROMPT_HEAD/PROMPT_TAIL dumps | Commit `c49274a` |
| `routes/cara_websocket.py` | Explicit `response.instructions` in greeting payload (diagnostic test) | Commit `c49274a` |
| `routes/voice.py:479-520` | Unprotected debug endpoints | Unknown (pre-existing) |
| `server.py:117-120` | `print("DEBUG Registered routes at startup:")` | Unknown (pre-existing) |
