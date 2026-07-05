# Incident Response Policy — DRAFT

**Organisation:** Ainm Technologies
**Date:** 2026-07-05
**Status:** DRAFT scaffold — based on actual v0.16.1-16.4 incident history
**Standard:** ISO 27001 A.5.24-A.5.28

---

## 1. Scope

This policy covers all information security incidents across the ainm product estate:
- ExecFlex (recruitment platform + voice screening)
- Ainm Advisory (HR advisor, BD agents, content engine)
- GovCompli (governance-platform — EU AI Act compliance)
- Pay Transparency Platform

## 2. Incident Classification

| Severity | Definition | Response Target | Example |
|----------|-----------|-----------------|---------|
| P1 — Critical | Service outage affecting revenue or data exposure | 30 minutes | Database breach, voice pipeline down, credentials exposed |
| P2 — High | Degraded service or compliance risk | 2 hours | AI agent failures, auth bypass discovered, data rights violation |
| P3 — Medium | Non-critical issue with workaround | 24 hours | Feature regression, monitoring gap, dependency vulnerability |
| P4 — Low | Cosmetic or minor issue | 1 week | UI inconsistency, dead code, documentation gap |

## 3. Incident Response Process

### 3.1 Detection

Sources:
- Uptime monitors (Cara voice prober, ExecFlex health endpoint)
- Error rate spikes in application logs
- User reports via support channels
- Automated alerts (Dependabot, GitHub Secret Scanner)
- Scheduled security audits (like this weekend's consolidation)

### 3.2 Triage

1. Assess severity using the classification table above
2. Determine blast radius (which products/users affected)
3. Check if the incident involves PII (triggers GDPR Art. 33 obligations)
4. Log the incident with timestamp, description, and initial assessment

### 3.3 Containment

Priority actions:
- Revoke compromised credentials immediately
- Disable affected endpoints if they expose data
- Switch to read-only mode if data integrity is at risk
- Notify affected users if PII is involved (within 72 hours per GDPR)

### 3.4 Resolution

1. Identify root cause
2. Develop and test fix
3. Deploy fix through standard change management process
4. Verify resolution via monitoring

### 3.5 Post-Incident Review

Within 5 business days:
- Document root cause in commit history or dedicated incident report
- Update KNOWN_DEFECTS.md if applicable
- Add to risk register if the root cause reveals a systemic issue
- Update this policy if the response process could be improved

## 4. Evidence from Prior Incidents

### v0.16.1-16.4 Incident Series (ExecFlex Voice Pipeline)

| Version | Issue | Root Cause | Resolution | Lessons Learned |
|---------|-------|-----------|------------|----------------|
| v0.16.1 | Voice greeting not delivered | `response.create` issued before listener thread started | Moved greeting after listener thread start (5621007) | Test event ordering in async pipelines |
| v0.16.2 | Diagnostic logging added | Insufficient observability of OpenAI Realtime events | Added event dump + prompt logging (c49274a) | Observability first, fix second |
| v0.16.3 | Audio never forwarded to caller | OpenAI event names changed in GA release | Updated event names to GA format (5f66f41) | Pin and verify third-party API versions |
| v0.16.4 | Uptime monitor added | No automated detection of voice pipeline failures | Email alerts on failure (1ab82e5) | Always add monitoring before declaring fixed |

**Pattern:** Each fix addressed the symptom, then a subsequent fix addressed the root cause. The series demonstrates iterative incident response but lacks a formal process.

## 5. Communication

| Audience | Channel | Timing |
|----------|---------|--------|
| Owner (Fionn) | Direct alert (uptime monitor email) | Immediate |
| Affected users | Product notification (if applicable) | Within 24 hours |
| Data Protection Authority | Formal notification (if PII breach) | Within 72 hours (GDPR Art. 33) |
| Customers | Status page / email (if service impact) | When impact is confirmed |

## 6. Roles

| Role | Person | Responsibilities |
|------|--------|-----------------|
| Incident Commander | Fionn (sole developer) | All: detection, triage, containment, resolution, review |

**Note:** Single-person team means no escalation path. Consider establishing an external advisor relationship for P1 incidents.

---

## Maintenance

Review this policy:
- After every P1 or P2 incident
- Quarterly as part of ISMS review
- When team size changes
