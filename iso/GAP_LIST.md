# ISO Certification Gap List

**Date:** 2026-07-05
**Status:** DRAFT — requires owner review and human decisions
**Source:** SoA, Risk Register, Asset Register, SECURITY_CLOSURE.md, AI Management System scaffold

---

## Reading Guide

Each gap is tagged:
- **HUMAN-DECISION** — requires owner judgement, cannot be automated
- **EXTERNAL** — requires external party (auditor, legal, DPA)
- **CODE** — solvable with a code/config change
- **PROCESS** — requires writing and adopting a documented process
- **COST** — requires budget allocation

Priority: CRITICAL > HIGH > MEDIUM > LOW

---

## CRITICAL — Block certification

| # | Gap | ISO Control | Type | Detail |
|---|-----|-------------|------|--------|
| G-001 | Exposed credentials in git history | A.5.17, R-001 | HUMAN-DECISION + CODE | SECURITY_CLOSURE REQUIRES_HUMAN-1. Rotate all 8 credential types in asset register. Consider git history scrub (BFG or filter-branch). Enable GitHub secret scanner. |
| G-002 | No DPIA for high-risk AI systems | Art. 35 GDPR, R-014 | EXTERNAL | ExecFlex recruitment and pay transparency AI are high-risk (EU AI Act Annex III, 4a/4b). DPIA required before processing. Engage data protection consultant. |
| G-003 | No MFA on admin accounts | A.5.17, R-020 | CODE | Supabase supports MFA. Enable for all admin accounts across all products. |
| G-004 | JWT secret possibly unset in production | A.8.5, R-004 | HUMAN-DECISION | Verify SUPABASE_JWT_SECRET is set in Render environment. If unset, tokens are unverified in production. |

## HIGH — Required for ISO 27001, addressable before audit

| # | Gap | ISO Control | Type | Detail |
|---|-----|-------------|------|--------|
| G-005 | No formal ISMS policy document | A.5.1 | PROCESS | CLAUDE.md files serve as informal policy. Need a top-level information security policy signed by owner. 2-page document covering scope, objectives, principles. |
| G-006 | No RACI / roles documentation | A.5.2 | PROCESS | Single developer — document that all ISMS roles (CISO, DPO, incident commander) are held by owner. Acknowledge the risk. |
| G-007 | No DPA contacts documented | A.5.5 | PROCESS | Document contacts: Irish DPC, ENISA, national CSIRT. Add to incident response plan. |
| G-008 | No credential rotation schedule | A.5.17, R-005 | PROCESS + CODE | Define rotation periods per credential type. Set calendar reminders. Track last-rotated date in asset register. |
| G-009 | No formal supplier security assessments | A.5.19 | PROCESS | Anthropic, Supabase, OpenAI, Stripe, Twilio, Render — review each provider's SOC 2/ISO 27001 certs. Document in supplier register. |
| G-010 | No BCP / documented RTO/RPO | A.5.30, R-016 | PROCESS | Document recovery targets per product. ExecFlex voice: RTO 30min. Database: RPO 24hr (Supabase PiTR). |
| G-011 | No database backup script | A.8.13, R-008 | CODE | Create automated pg_dump script. Test restore procedure. Document in BCP. |
| G-012 | No centralised logging/SIEM | A.8.15 | COST + CODE | Application logs exist but no central aggregation. Options: Datadog, Grafana Cloud, ELK. |
| G-013 | No penetration testing | A.8.29 | EXTERNAL + COST | Security tests exist (12 in ExecFlex, 42 in governance-platform) but no external pentest. Engage before certification. |
| G-014 | No independent security review | A.5.35 | EXTERNAL + COST | Required for certification. Engage external auditor. |
| G-015 | Voice pipeline error alerting incomplete | A.8.16, R-009 | CODE | Uptime monitor exists (v0.16.4) but error rate alerting is missing. Add error counter + alert threshold. |

## MEDIUM — Strengthens posture, may defer to Phase 2

| # | Gap | ISO Control | Type | Detail |
|---|-----|-------------|------|--------|
| G-016 | No formal data classification scheme | A.5.12, A.5.13 | PROCESS | Asset register has ad-hoc labels (Confidential, Highly Confidential). Formalise into 4-tier scheme with handling rules per tier. |
| G-017 | No formal information transfer policy | A.5.14 | PROCESS | HTTPS everywhere. Document which data crosses which boundaries (e.g., prompts sent to Anthropic may contain PII). |
| G-018 | No formal records retention policy | A.5.33 | PROCESS + HUMAN-DECISION | How long to keep: AI decision logs, screening sessions, HR conversations, assessment data? GDPR data minimisation requires defined periods. |
| G-019 | No formal legal register | A.5.31 | PROCESS | Document applicable regulations: GDPR, EU AI Act, Pay Transparency Directive, Employment Equality Acts. Track implementation status per regulation. |
| G-020 | hr-advisory-agent has zero automated tests | A.8.25 | CODE | Only product with no test suite. Add at minimum: auth tests, agent smoke tests, privacy toggle verification. |
| G-021 | No formal change approval process | A.8.32 | PROCESS | CHANGE_MANAGEMENT.md drafted. Need to formalise and evidence consistent use. |
| G-022 | Single-box deployment (no redundancy) | A.8.14, R-002 | COST + CODE | All products run on single instances. Container orchestration or multi-instance deploy needed for HA. |
| G-023 | Dev/prod environment separation | A.8.31 | CODE | Same codebase serves dev and prod via .env config. No dedicated staging for ExecFlex or ainm. |
| G-024 | Model provider DPA review | AIR-007 | EXTERNAL | Review Anthropic and OpenAI data processing agreements. Confirm zero-retention API mode is contractually binding. |

## LOW — Nice to have, not blocking

| # | Gap | ISO Control | Type | Detail |
|---|-----|-------------|------|--------|
| G-025 | No clear desk/screen policy | A.7.7 | PROCESS | Single developer, remote. Low risk. Document minimal policy. |
| G-026 | No endpoint device management | A.8.1 | PROCESS | Development on personal Windows device. No MDM. Document device security measures (BitLocker, Windows Hello). |
| G-027 | No NDAs with any party | A.6.6 | PROCESS | Consider NDAs with contractors, beta testers, or partners as company grows. |
| G-028 | Dead code in execo-bridge | A.8.25 | CODE | ~25 legacy components. Low security risk. Prune in cleanup commit. |

---

## Summary

| Priority | Count | Types |
|----------|-------|-------|
| CRITICAL | 4 | 2 HUMAN-DECISION, 1 EXTERNAL, 1 CODE |
| HIGH | 11 | 5 PROCESS, 3 CODE, 2 EXTERNAL+COST, 1 COST+CODE |
| MEDIUM | 9 | 5 PROCESS, 2 CODE, 1 COST+CODE, 1 EXTERNAL |
| LOW | 4 | 3 PROCESS, 1 CODE |
| **Total** | **28** | |

## Recommended Sequence

1. **This week:** G-001 (rotate credentials), G-003 (enable MFA), G-004 (verify JWT secret)
2. **This month:** G-005 (ISMS policy), G-006 (roles), G-007 (DPA contacts), G-008 (rotation schedule), G-010 (BCP), G-011 (backup script)
3. **Before audit:** G-002 (DPIA), G-009 (supplier assessments), G-013 (pentest), G-014 (independent review)
4. **Ongoing:** G-012 (SIEM), G-016 (classification), G-018 (retention), G-020 (ainm tests)
