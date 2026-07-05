# PROD_CLEANUP.md — Production Hygiene Findings

Generated 2026-07-05 as part of estate consolidation run.
Owner review required before acting on any finding.

---

## CRITICAL — Security

### 1. governance-platform snapshot endpoint is public and unauthenticated

**Location:** `governance-platform/backend/app/routers/snapshot.py`
**Evidence:** `POST /snapshot/score` accepts arbitrary JSON, calls Claude for gap analysis, stores email in `snapshot_leads` table. No auth, no rate limiting.
**Risk:** Prompt injection via crafted answers, cost amplification via scripted repeated calls, email harvesting from `snapshot_leads`.
**Remediation:**
- Add rate limiting (e.g. 5 requests/IP/hour)
- Add CAPTCHA or honeypot for the email field
- Add input validation (restrict `business_functions` to known enum values)
- Consider moving to ExecFlex with org-scoped auth

### 2. governance-platform hardcodes model names in AI service

**Location:** `governance-platform/backend/app/services/ai_service.py` — lines 12, 30, 80, 117, 237, 373
**Evidence:** `claude-opus-4-6` hardcoded in 4 places, `claude-sonnet-4-6` in 2.
**Risk:** If Anthropic deprecates these model IDs, all AI functions break silently (API returns 400).
**Remediation:** Ported compliance module in agentic-core uses prompt metadata routing — no hardcoded models. Governance-platform should adopt the same pattern when next touched.

### 3. No credential rotation evidence

**Finding:** No documented credential rotation schedule for Anthropic API keys, Stripe keys, or Supabase service role keys across any repo.
**Remediation:** Add rotation dates to a shared secrets register. Consider using environment-specific keys with shorter lifetimes.

---

## HIGH — Reliability

### 4. Single-box deployment (ExecFlex)

**Evidence:** ExecFlex backend runs on a single Supabase-hosted instance. No health check endpoint beyond the smoke test bypass. No load balancer, no blue-green deploy.
**Risk:** Single point of failure. If the process crashes, voice screening goes down with no automatic recovery.
**Remediation:**
- Add a `/health` endpoint that checks DB connectivity
- Consider deploying behind a reverse proxy with health checks
- Add process supervisor (systemd, PM2, or container orchestration)

### 5. No automated backup script

**Evidence:** No `backups/` directory in execflex-backend. No `pg_dump` scripts. No cron job evidence.
**Finding:** Supabase provides point-in-time recovery on paid plans, but no local backup script exists for disaster recovery testing.
**Remediation:** Create a `scripts/backup.sh` that runs `pg_dump` to a dated file. Test restore process at least once.

### 6. Voice pipeline error handling

**Location:** `execflex-backend/routes/voice_websocket.py`, `routes/cara_voice.py`
**Finding:** OpenAI Realtime WebSocket errors are logged but not reported to any monitoring system. A sustained OpenAI outage would silently fail all voice screenings.
**Remediation:** Add error rate tracking and alerting (even a simple counter endpoint that the uptime monitor checks).

---

## MEDIUM — Code Hygiene

### 7. Dead code in execo-bridge (~25 components)

**Location:** `execo-bridge/src/pages/ExecutiveOnboarding.tsx` + 11 step files, `src/components/executive-matching/`, `src/components/post-role/`, `src/lib/api.ts`
**Evidence:** Tagged DEAD in ESTATE_MAP. Reference the pre-rebuild API client (`api.ts`). No routes point to them.
**Remediation:** Confirm no routes import them, then delete in a single cleanup commit. Reduces bundle and confusion.

### 8. governance-platform RAG service has no equivalent in agentic-core

**Location:** `governance-platform/backend/app/services/rag_service.py`
**Finding:** ChromaDB-based document intelligence (375-word chunks, 38-word overlap). Used in Stage D assessment completion. No agentic-core equivalent exists. If governance-platform is decommissioned, this capability is lost.
**Decision needed:** Build a RAG primitive in agentic-core, or keep governance-platform alive until RAG is ported.

### 9. ainm `_llm_client.py` duplicates agentic-core

**Location:** `hr-advisory-agent/backend/app/agents/_llm_client.py`
**Finding:** Custom Anthropic wrapper that duplicates `AnthropicLLMClient` from agentic-core. Maintenance burden: model changes must be made in two places.
**Remediation:** Replace with `from agentic_core.primitives.llm.anthropic_client import AnthropicLLMClient`. Requires adding agentic-core as a dependency to ainm.

### 10. ainm transcript visibility — admin can view all conversations

**Location:** `hr-advisory-agent/backend/app/admin/router.py` — `GET /faq/conversations?employee_id=X`
**Finding:** Company admins can view full conversation transcripts for any employee with no privacy filtering. No distinction between employee-initiated and employer-initiated conversations.
**Risk:** GDPR concern — employee conversations about personal HR matters (grievances, mental health, workplace issues) are visible to their employer's admin.
**Remediation:** Add per-company privacy toggle defaulting to OFF for employee-initiated conversations. Provide aggregate-only view (topics/volume, no transcripts) when privacy is on. See Phase 4 cara-privacy branch.

---

## LOW — Polish

### 11. ExecFlex frontend directory doesn't exist separately

**Finding:** Earlier references to "execflex-frontend" are incorrect — the frontend is `execo-bridge` on `rebuild-v1`. No separate frontend repo exists. Update any documentation that references `execflex-frontend`.

### 12. No KNOWN_DEFECTS.md in governance-platform or transparency-platform

**Finding:** Neither live product has a KNOWN_DEFECTS.md file. Known issues exist only in git issues (if any) or team memory.
**Remediation:** Create minimal KNOWN_DEFECTS.md in each repo listing active workarounds and known limitations.

### 13. Test coverage gaps

| Repo | Tests | Coverage Notes |
|------|-------|---------------|
| execflex-backend | 217 (rebuild-v1) | Good for matching, screening, syndication. No tests for voice WebSocket handler. |
| agentic-core | 736 (recruitment-agents) | Comprehensive. Includes 131 new compliance tests. |
| governance-platform | Unknown | No test runner evidence found in quick audit. |
| hr-advisory-agent | Unknown | No test runner evidence found in quick audit. |

---

## ACTION PRIORITY

1. **Rate-limit the governance-platform snapshot endpoint** (Critical, <1 hour)
2. **Document credential rotation schedule** (Critical, <1 hour)
3. **Add ExecFlex health endpoint** (High, <30 min)
4. **Cara privacy toggle** (Medium, in progress on cara-privacy branch)
5. **Delete dead execo-bridge components** (Medium, <30 min)
6. **Replace ainm `_llm_client.py`** (Medium, <1 hour)
7. **Decide on RAG primitive** (Medium, owner decision needed)
