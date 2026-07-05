# Risk Register — ISO 27001 / ISO 42001

**Sources:** PROD_CLEANUP.md, SECURITY_CLOSURE.md, ESTATE_MAP.md, incident history, this weekend's audit findings
**Date:** 2026-07-05
**Status:** DRAFT scaffold

---

## Risk scoring

**Likelihood:** 1 (Rare) — 2 (Unlikely) — 3 (Possible) — 4 (Likely) — 5 (Almost certain)
**Impact:** 1 (Negligible) — 2 (Minor) — 3 (Moderate) — 4 (Major) — 5 (Catastrophic)
**Risk = Likelihood × Impact**

---

## Active Risks

| ID | Risk | Category | L | I | Score | Owner | Treatment | Status | Source |
|----|------|----------|---|---|-------|-------|-----------|--------|--------|
| R-001 | Credentials exposed in git history allow unauthorised access | Confidentiality | 3 | 5 | **15** | Fionn | Rotate all exposed keys. Scrub git history. Enable secret scanner. | OPEN | SECURITY_CLOSURE REQUIRES_HUMAN-1 |
| R-002 | Single-box deployment — full service outage on process crash | Availability | 3 | 4 | **12** | Fionn | Add health checks, process supervisor, or container orchestration. | OPEN | PROD_CLEANUP #4 |
| R-003 | governance-platform snapshot endpoint abused for cost amplification | Financial | 3 | 3 | **9** | Fionn | Rate limiting applied (5/IP/hr). Monitor for abuse patterns. | MITIGATED | PROD_CLEANUP #1 → FIX-1 |
| R-004 | JWT secret unset in production — tokens unverified | Integrity | 2 | 5 | **10** | Fionn | Verify SUPABASE_JWT_SECRET set in prod. Add startup validation. | OPEN | SECURITY_CLOSURE REQUIRES_HUMAN-3 |
| R-005 | No credential rotation schedule — stale keys accumulate | Confidentiality | 4 | 3 | **12** | Fionn | Document rotation schedule. Set calendar reminders. | OPEN | PROD_CLEANUP #3 |
| R-006 | Smoke test bypass could be enabled in production | Integrity | 2 | 5 | **10** | Fionn | Production guard applied (FIX-3). Verify env vars in prod. | MITIGATED | SECURITY_CLOSURE FIX-3 |
| R-007 | Cara transcript visibility — admin can see employee HR conversations | Privacy | 3 | 4 | **12** | Fionn | Privacy toggle implemented (default OFF). Per-company, not per-conversation. | MITIGATED | PROD_CLEANUP #10 |
| R-008 | No automated database backup script | Availability | 2 | 5 | **10** | Fionn | Create pg_dump script. Test restore. Supabase PiTR covers paid plans. | OPEN | PROD_CLEANUP #5 |
| R-009 | Voice pipeline errors silently fail — no alerting | Availability | 3 | 3 | **9** | Fionn | Add error rate counter. Wire to uptime monitor. | OPEN | PROD_CLEANUP #6 |
| R-010 | governance-platform hardcoded model names | Operational | 3 | 2 | **6** | Fionn | Migrated to env-var-based model routing on overhaul-2026-07. | MITIGATED | PROD_CLEANUP #2 |
| R-011 | No database-level RLS — application-layer auth only | Confidentiality | 2 | 4 | **8** | Fionn | Accept risk. Application layer verified by tests. RLS is defence-in-depth enhancement. | ACCEPTED | SECURITY_CLOSURE audit |
| R-012 | agentic-core branch pin (not tagged release) | Operational | 3 | 3 | **9** | Fionn | Tag v0.17.0 before any production deployment. Multi-consumer gate enforced. | OPEN | DECISIONS D-27 |
| R-013 | Three separate LLM client implementations | Operational | 2 | 2 | **4** | Fionn | governance-platform migrated to env-var routing. ainm migration deferred. | PARTIALLY MITIGATED | ESTATE_MAP #2 |
| R-014 | No DPIA for AI systems processing personal data | Compliance | 3 | 4 | **12** | Fionn | Conduct DPIA for ExecFlex candidate processing and Cara HR conversations. | OPEN | EU AI Act Art. 35 GDPR |
| R-015 | No incident response plan | Operational | 3 | 3 | **9** | Fionn | Draft IRP based on v0.16.x incident history. See iso/INCIDENT_RESPONSE.md. | MITIGATED | SoA A.5.24 |
| R-016 | No business continuity plan | Availability | 2 | 4 | **8** | Fionn | Document RTO/RPO. Identify critical paths. | OPEN | SoA A.5.30 |
| R-017 | governance-platform RAG service has no agentic-core equivalent | Operational | 2 | 3 | **6** | Fionn | Document as prerequisite before governance-platform decommission. | ACCEPTED | DECISIONS D-37 |
| R-018 | Dead code in execo-bridge (~25 components) | Operational | 1 | 2 | **2** | Fionn | Low priority. Delete in cleanup commit. | ACCEPTED | PROD_CLEANUP #7 |
| R-019 | Prompt injection via crafted snapshot answers | Integrity | 2 | 3 | **6** | Fionn | Input validation applied (FIX-1). LLM output not used for system decisions. | MITIGATED | PROD_CLEANUP #1 |
| R-020 | No MFA on any admin account | Confidentiality | 3 | 4 | **12** | Fionn | Supabase supports MFA. Enable for all admin accounts. | OPEN | SoA A.5.17 |

---

## Risk Heat Map

```
Impact →    1         2         3         4         5
Likelihood
    5                                                
    4                          R-005                  
    3              R-010  R-003,R-009  R-007,R-014  R-001
                               R-012,R-015          
    2              R-013  R-017,R-019  R-008,R-011  R-004,R-006
                                       R-016,R-020  
    1              R-018                              
```

## Treatment Priority

1. **R-001** (Score 15): Rotate exposed credentials — IMMEDIATE
2. **R-002** (Score 12): Single-box deployment — plan redundancy
3. **R-005** (Score 12): Credential rotation schedule — establish this week
4. **R-007** (Score 12): Cara privacy — toggle implemented, monitor
5. **R-014** (Score 12): DPIA — engage data protection consultant
6. **R-020** (Score 12): MFA — enable on Supabase admin accounts
7. **R-004** (Score 10): JWT secret verification — confirm in production
