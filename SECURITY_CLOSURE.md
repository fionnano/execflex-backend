# SECURITY_CLOSURE.md — Estate Security Hardening

Generated 2026-07-05 as part of estate consolidation. Each fix includes file:line. Items requiring human/deploy action are marked REQUIRES_HUMAN.

---

## FIXES APPLIED (code-side, no deploy needed)

### FIX-1: governance-platform snapshot endpoint rate-limited + validated
- **File:** `governance-platform/backend/app/routers/snapshot.py:57-66`
- **Branch:** `security-hardening`
- **What:** Added 5 requests/IP/hour rate limit, input validation against known enum values, request logging with IP, exception logging instead of silent swallow.
- **Addresses:** PROD_CLEANUP #1 (public unauthenticated snapshot endpoint)

### FIX-2: governance-platform auth endpoints rate-limited
- **File:** `governance-platform/backend/app/routers/auth.py:17-19`
- **Branch:** `security-hardening`
- **What:** Login: 10 requests/IP per 5 minutes. Register: 5 requests/IP per hour. Prevents brute-force and registration spam.
- **New dependency:** `governance-platform/backend/app/core/rate_limiter.py` — in-memory sliding-window rate limiter, thread-safe, keyed by client IP with X-Forwarded-For support.

### FIX-3: execflex-backend smoke test bypass blocked in production
- **File:** `execflex-backend/utils/auth_helpers.py:60-64`
- **Branch:** `security-hardening`
- **What:** When `FLASK_ENV=production` or `APP_ENV=production`, the `X-Smoke-Test` header bypass is ignored even if the secret is configured. Logs a warning instead of authenticating.
- **Rationale:** Smoke test bypass is needed for CI/CD but must never activate in production.

---

## ITEMS REQUIRING HUMAN/DEPLOY ACTION

### REQUIRES_HUMAN-1: Rotate credentials exposed in git history
- **Repo:** execflex-backend
- **Evidence:** Commits `f560e76` and `490df8c` in git history contain `.env` with real credentials:
  - `SUPABASE_SERVICE_KEY` (admin-level JWT)
  - `EMAIL_PASS` (Gmail app password)
  - `APOLLO_API_KEY`
- **Current status:** `.env` is properly in `.gitignore` and NOT tracked on current branch. Credentials exist only in git history.
- **Action required:**
  1. Rotate Supabase service key in Supabase dashboard
  2. Rotate Gmail app password
  3. Rotate Apollo API key
  4. Consider `git filter-repo` to scrub history (destructive — requires force-push)

### REQUIRES_HUMAN-2: governance-platform .env exists on disk (not committed)
- **Repo:** governance-platform
- **Evidence:** `.env` file exists on disk with real Anthropic API key (`sk-ant-api03-...`). File is in `.gitignore` and NOT tracked by git.
- **Current status:** Safe from git exposure. Key exists only on developer's machine and deployment.
- **Action required:** Verify the Anthropic key hasn't been shared. Document rotation date.

### REQUIRES_HUMAN-3: Set SUPABASE_JWT_SECRET in production
- **Repo:** execflex-backend
- **File:** `utils/auth_helpers.py:82-97`
- **Evidence:** If `SUPABASE_JWT_SECRET` is not set, JWT signature verification is disabled (dev mode). Tokens are decoded without signature check.
- **Action required:** Confirm `SUPABASE_JWT_SECRET` is set in all production environments. Add startup health check that fails if missing in production.

### REQUIRES_HUMAN-4: governance-platform default JWT secret
- **Repo:** governance-platform
- **File:** `backend/app/config.py:15`
- **Evidence:** `secret_key: str = "change-me-in-production-use-a-long-random-string"`. If `.env` doesn't override this, JWTs are predictable.
- **Action required:** Verify `.env` in production overrides this value with a strong random secret.

### REQUIRES_HUMAN-5: Deploy security-hardening branches
- **Action required:** After review, merge `security-hardening` branches into their respective main/production branches:
  - governance-platform: `security-hardening` → `main`
  - execflex-backend: `security-hardening` → `rebuild-v1` (then to `main` at next release)

### REQUIRES_HUMAN-6: Enable GitHub Secret Scanner
- **Action required:** Enable GitHub's push protection and secret scanning on all repos to prevent future credential commits.

---

## AUDIT RESULTS — NO ACTION NEEDED

### SQL Injection: CLEAR
All repos use parameterised queries. governance-platform uses SQLAlchemy ORM with `select().where()`. execflex-backend uses Supabase SDK `.eq()` filters. Snapshot endpoint uses named parameter binding (`:id`, `:email`).

### RLS Policies: APPLICATION-LEVEL ONLY
No database-level RLS policies in any repo. All repos enforce org isolation at the application layer:
- execflex-backend: `extract_org_context()` reads `org_id` from JWT claims; all v1 API routes use `@require_org()`
- governance-platform: `organisation_id` filtering via `Depends(get_current_active_user)`
- transparency-platform: company-scoped queries with tenant isolation tests
- hr-advisory-agent: `company_id` from auth context

Database-level RLS would be a defence-in-depth improvement but is not a vulnerability given the current auth layer.

### Eval/Exec/Subprocess: CLEAR
No `eval()`, `exec()`, or `subprocess` calls with user input found in any repo.

### Webhook Security: ADEQUATE
- governance-platform Stripe webhook: Stripe signature verification
- execflex-backend Twilio webhook: Twilio request validation
- transparency-platform: service-key auth for admin endpoints

### transparency-platform: ALREADY HARDENED
Rate limiting already implemented in `backend/app/core/rate_limit.py`:
- AI endpoints: 3 calls/60s per user
- Auth login: 5/min, register: 1/hour
- Lead capture: 5/hour
- DFY pack: 1/hour (most expensive)

No additional security fixes needed for transparency-platform.

---

## SUMMARY

| Finding | Severity | Status |
|---------|----------|--------|
| Snapshot endpoint unauthenticated + no rate limit | Critical | **FIXED** (FIX-1) |
| Auth endpoints no brute-force protection | High | **FIXED** (FIX-2) |
| Smoke test bypass reachable in prod | High | **FIXED** (FIX-3) |
| Credentials in git history | Critical | **REQUIRES_HUMAN** (rotation) |
| JWT secret may be unset in prod | High | **REQUIRES_HUMAN** (verification) |
| Default JWT signing key | High | **REQUIRES_HUMAN** (verification) |
| No database-level RLS | Medium | **DOCUMENTED** (app-layer sufficient) |
| SQL injection | — | **CLEAR** |
| Code injection | — | **CLEAR** |
