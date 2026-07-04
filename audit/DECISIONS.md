# Audit Decisions Log

Every assumption made during the audit is recorded here.

## 2026-07-04

### D-001: Scope of "both repos"
The backend is at `c:\Users\fionn\execflex-backend` (Python/Flask). The frontend is at `c:\Users\fionn\execo-bridge` (TypeScript/Vite/React). Both are audited.

### D-002: "~11k data points" interpretation
The owner mentioned ~11k recruitment/placement data points. From code analysis, the primary tables contributing to this are `people_profiles`, `opportunities`, `interactions`, `placements`, and `outbound_call_jobs`. Without querying the database, I cannot confirm the exact breakdown. I assume the bulk is `people_profiles` records (candidates) plus `interactions` (screening calls, introductions).

### D-003: No database access
Hard constraint. All schema reconstruction is from code-level references (Supabase SDK calls, SQL files in the frontend repo, TypeScript type definitions). Column types are inferred from usage patterns and naming conventions. Some columns may exist in the database but never be referenced in application code.

### D-004: Security findings are code-level only
I cannot test for runtime vulnerabilities (e.g., actual RLS bypass, network exposure). Findings are limited to what is visible in source code: committed secrets, missing auth checks, CORS configuration, input validation gaps.

### D-005: Cost estimates are approximate
Without access to Render, Stripe, Twilio, OpenAI, or ElevenLabs dashboards, cost figures are based on public pricing and code-inferred usage patterns.

### D-006: "Dead code" definition
Code is classified as dead/abandoned if: (a) it is explicitly disabled in code (e.g., Apollo service), (b) it is a stub with no implementation, (c) it references features with no frontend integration visible in the execo-bridge repo, or (d) it is seeding/demo code.

### D-007: Email sender infrastructure
The backend uses direct SMTP via `modules/email_sender.py` with `EMAIL_USER` and `EMAIL_PASSWORD` env vars (likely Gmail app password). No dedicated transactional email service (SendGrid, Resend, etc.) is used.

### D-008: Render deployment model
Procfile uses `gunicorn server:app --workers 1 --threads 16 --timeout 120`. Single-worker model chosen because of WebSocket requirements (Flask-Sock). Background threads run in-process (voice monitor, session cleanup). No separate worker dyno.

### D-009: Frontend SQL files are admin scripts, not migrations
The `.sql` files in the execo-bridge root (ADD_ADMINS_MANUAL.sql, SEED_*.sql, etc.) appear to be manual admin scripts, not automated migrations. The `supabase/` directory likely contains the actual migration files.

### D-010: Cara vs Aidan voice paths
Two distinct voice systems exist: Aidan (Twilio-based, outbound calls via `voice_websocket.py`) and Cara (browser-based, `cara_websocket.py`). Both use OpenAI Realtime API but with different audio codecs (G.711 mu-law vs PCM 24kHz) and different session management patterns.

### D-011: Voice monitor cost estimate
I estimated $5-15/month for the voice monitor probes based on: 288 probes/day × ~5-10 seconds of OpenAI Realtime session per probe × OpenAI Realtime pricing (~$0.06/min input + $0.24/min output as of mid-2025). The actual cost depends on how much audio OpenAI generates before the probe disconnects. This is a rough estimate.

### D-012: Dataset volume estimates
Without database access, record counts are estimated from code patterns: bulk upload features suggest hundreds-to-thousands of profiles; placement tracking suggests a smaller set of confirmed hires; the append-only interactions table accumulates rapidly (multiple per candidate). The "~11k" figure the owner stated likely includes interactions (the highest-volume table).

### D-013: k-Anonymity suppression estimate
The 60-80% suppression estimate comes from: ~3,000 profiles distributed across 20 role categories × 5 experience bands × 16 industries × 4 regions × 4 engagement types = 25,600 possible cells. Average cell occupancy = 0.12 records/cell. Most cells will have <5 records. This is worst-case; many dimensions will collapse (e.g., most records are Ireland + full_time) which improves occupancy in popular cells.

### D-014: Revival time estimate
The 5-10 day estimate assumes: (a) one experienced developer already familiar with the codebase, (b) no new features — just fix-and-verify existing functionality, (c) security fixes are straightforward (remove/gate endpoints, enforce secrets), (d) voice bug is known and documented. A developer coming in cold would need additional time to understand the system.

### D-015: Frontend hosting unknown
The frontend deployment target (Netlify, Vercel, Cloudflare Pages, or other) is not visible in either repository. The frontend is a Vite SPA that can be hosted anywhere. Cost is likely $0-20/month.

### D-016: debug=True severity downgrade
The security agent flagged `debug=True` as CRITICAL. I downgraded to MEDIUM because it's inside `if __name__ == "__main__"` which gunicorn (the production runner, per Procfile) does not execute. The risk exists only if someone runs `python server.py` directly in the production container.
