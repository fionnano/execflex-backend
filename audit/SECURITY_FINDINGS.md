# Security Findings

Audit date: 2026-07-04
Scope: Code-level analysis of execflex-backend and execo-bridge repositories. No runtime testing, no database access, no network scanning.

---

## CRITICAL

### S-001: Unprotected debug endpoints expose call logs
- **Location**: `routes/voice.py:479-520`
- **Issue**: `GET /voice/debug/handler-log/<call_sid>` and `GET /voice/debug/latest-log` are publicly accessible with no authentication. They read files from `/tmp/openai_handler_*.log` which may contain conversation transcripts, phone numbers, and other personal data.
- **Impact**: Information disclosure of call transcripts and PII to any internet user who discovers the endpoint.
- **Recommendation**: Remove entirely or gate behind `@require_admin`.

### S-002: Stripe webhook signature verification disabled when secret not configured
- **Location**: `routes/billing.py:118-131`
- **Issue**: If `STRIPE_WEBHOOK_SECRET` is not set, the webhook handler prints a warning but still processes the event. An attacker could forge Stripe webhook payloads to create fake subscriptions, mark payments as succeeded, or manipulate placement records.
- **Impact**: Financial fraud — fake payment confirmations, unauthorized subscription upgrades.
- **Recommendation**: Fail hard (return 500) if `STRIPE_WEBHOOK_SECRET` is not configured in production.

### S-003: SQL filter injection via string interpolation
- **Location**: `routes/screening.py:280`
- **Code**: `.or_(f"role_id.eq.{role_id},role_title.eq.{role_id}")`
- **Issue**: The `role_id` URL parameter is directly interpolated into a Supabase PostgREST filter string. While PostgREST has some built-in protections, this pattern bypasses parameterized query safety and could allow filter injection.
- **Impact**: Potential data exposure or filter bypass.
- **Recommendation**: Use parameterized `.or_()` calls or validate `role_id` as UUID before interpolation.

---

## HIGH

### S-004: Twilio signature verification bypass
- **Location**: `utils/twilio_helpers.py:32-34`
- **Issue**: Signature verification returns `True` (allow) when `TWILIO_AUTH_TOKEN` is not set or `APP_ENV == "dev"`. If the production environment has `APP_ENV=dev` or the token is missing, any HTTP client can forge Twilio webhooks and trigger outbound calls, status updates, or media stream connections.
- **Impact**: Unauthorized call initiation, webhook spoofing.
- **Recommendation**: Require `TWILIO_AUTH_TOKEN` in production; never skip verification based on `APP_ENV`.

### S-005: Debug endpoints read arbitrary log files
- **Location**: `routes/voice.py:479-520`
- **Issue**: The `call_sid` parameter is used to construct a file path (`/tmp/openai_handler_{call_sid}.log`). While the filename pattern limits traversal, the endpoint is completely unauthenticated (see S-001).
- **Impact**: Combined with S-001, allows unauthenticated log retrieval.

### S-006: JWT signature verification skipped when secret not configured
- **Location**: `utils/auth_helpers.py` (via `config/app_config.py`)
- **Issue**: If `SUPABASE_JWT_SECRET` is not set, JWT tokens are decoded without signature verification. A warning is logged once, but requests continue to be authenticated based on the unverified `sub` claim. An attacker could craft a JWT with any user ID.
- **Impact**: Full authentication bypass — impersonate any user including admins.
- **Recommendation**: Fail startup if `SUPABASE_JWT_SECRET` is not set in production (`APP_ENV=production`).

### S-007: Extensive print statements with potentially sensitive data
- **Location**: Multiple files — `routes/roles.py:25`, `routes/screening.py:25`, `routes/billing.py` (80+ statements), `routes/onboarding.py`
- **Issue**: Request payloads, business logic details, and debug information are printed to stdout. On Render, stdout goes to the logging service which may be accessible to team members or log aggregation tools.
- **Impact**: Information disclosure in log aggregation. Payloads may contain names, emails, phone numbers.
- **Recommendation**: Replace `print()` with structured logging; sanitize PII from log output.

---

## MEDIUM

### S-008: In-memory rate limiting only
- **Location**: `utils/rate_limiting.py:26`
- **Issue**: Flask-Limiter uses `memory://` storage backend. Rate limits are per-process and reset on restart. With `--workers 1` this is functional but fragile — any restart clears all rate limit state.
- **Impact**: Rate limiting provides weaker protection than expected during deployments or restarts.

### S-009: Development CORS origins hardcoded in production config
- **Location**: `server.py:59-61`
- **Issue**: `http://localhost:5173` and `http://localhost:3000` are always in the CORS allow list, including production. An attacker running a local server on these ports could make cross-origin requests with credentials.
- **Impact**: Low practical risk (requires victim to have localhost:5173 running), but violates principle of least privilege.
- **Recommendation**: Conditionally include localhost origins based on `APP_ENV`.

### S-010: Duplicate CORS configuration
- **Location**: `routes/cara_voice.py:24-31` (separate from `server.py:53-68`)
- **Issue**: Cara voice routes maintain a separate allowed origins set. These could diverge from the main CORS config, creating inconsistent access control.

### S-011: No rate limiting on candidate status token endpoint
- **Location**: `routes/screening.py` — `/screening/candidate-status?token=<uuid>`
- **Issue**: UUID tokens for candidate portal access are not rate-limited. While UUID space is large (2^122), there is no brute-force protection.
- **Impact**: Low — UUID collision probability is negligible, but defense-in-depth warrants rate limiting.

### S-012: `debug=True` in `app.run()` block
- **Location**: `server.py:124`
- **Issue**: `app.run(host="0.0.0.0", port=PORT, debug=True)` — this is inside `if __name__ == "__main__"` which gunicorn does not execute. However, if anyone runs `python server.py` directly in production, the Flask debugger (with interactive console) would be exposed.
- **Impact**: RCE if the server is started via `python server.py` instead of gunicorn. Low risk in current deployment model.
- **Recommendation**: Set `debug=False` or use `os.getenv("FLASK_DEBUG", "0") == "1"`.

---

## LOW / INFORMATIONAL

### S-013: Supabase project ID exposed in frontend code
- **Location**: `execo-bridge/.env` or environment config — `VITE_SUPABASE_PROJECT_ID=krzacydualjpsapffpfm`
- **Issue**: Project ID is publicly visible (expected for client-side Supabase usage). Not a vulnerability if RLS is properly configured.

### S-014: No `.env` files committed to backend repo
- **Status**: GOOD — `.gitignore` correctly excludes `.env`. `.env.example` contains only placeholder values.

### S-015: Service-to-service auth uses constant-time comparison
- **Location**: `utils/auth_helpers.py:31`
- **Status**: GOOD — `hmac.compare_digest()` prevents timing attacks on service key validation.

### S-016: Stripe publishable key in frontend environment
- **Location**: `execo-bridge` env config — `VITE_STRIPE_PUBLISHABLE_KEY=pk_live_...`
- **Status**: Expected — publishable keys are designed for client-side use.

### S-017: Append-only interactions table
- **Status**: GOOD — Database-level triggers prevent UPDATE/DELETE on the `interactions` table, providing a tamper-resistant audit trail.

### S-018: EU AI Act compliance infrastructure
- **Status**: GOOD — `screening_bias_audit` table tracks fairness metrics, AI disclosure, and consent per screening call. `screening_feedback_requests` supports right-to-explanation (Article 86).

---

## Summary by Severity

| Severity | Count | Action Required |
|----------|-------|-----------------|
| CRITICAL | 3 | Fix before any production use or data processing |
| HIGH | 4 | Fix within 1-2 weeks |
| MEDIUM | 5 | Fix within 1 month |
| LOW/INFO | 6 | No immediate action; documented for awareness |
