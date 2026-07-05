# Statement of Applicability (SoA) — ISO/IEC 27001:2022

**Organisation:** Ainm Technologies (trading as ExecFlex, Ainm Advisory, GovCompli)
**Scope:** All information systems supporting the ainm product estate (6 codebases, 57 AI agents)
**Date:** 2026-07-05
**Status:** DRAFT — scaffold for certification preparation, not a certified document

---

## How to read this document

Each Annex A control is marked:
- **APPLICABLE** — relevant to our operations; implementation status noted
- **NOT APPLICABLE** — with justification for exclusion
- **PARTIAL** — applicable but not fully implemented

---

## A.5 Organisational Controls

| Control | Title | Applicability | Status | Justification / Evidence |
|---------|-------|--------------|--------|--------------------------|
| A.5.1 | Policies for information security | APPLICABLE | PARTIAL | CLAUDE.md files per repo define constraints. No formal ISMS policy document exists. |
| A.5.2 | Information security roles and responsibilities | APPLICABLE | PARTIAL | Single developer operates all systems. No formal RACI. |
| A.5.3 | Segregation of duties | APPLICABLE | PARTIAL | Code review via Claude Code. No human second-pair review on deploys. |
| A.5.4 | Management responsibilities | APPLICABLE | PARTIAL | Owner is sole decision-maker. No formal management review cycle. |
| A.5.5 | Contact with authorities | APPLICABLE | NOT IMPLEMENTED | No documented contacts for DPC, ENISA, or national CSIRT. |
| A.5.6 | Contact with special interest groups | NOT APPLICABLE | — | No industry group memberships relevant to ISMS. |
| A.5.7 | Threat intelligence | APPLICABLE | PARTIAL | Dependabot alerts on GitHub. No formal threat intelligence feed. |
| A.5.8 | Information security in project management | APPLICABLE | PARTIAL | Security considerations in DECISIONS.md. No formal security gate in SDLC. |
| A.5.9 | Inventory of information and other associated assets | APPLICABLE | IMPLEMENTED | ESTATE_MAP.md + CANONICAL_AGENT_COUNT.md |
| A.5.10 | Acceptable use of information and other associated assets | APPLICABLE | PARTIAL | Implied in CLAUDE.md constraints. No formal AUP. |
| A.5.11 | Return of assets | NOT APPLICABLE | — | Single developer, no employee offboarding process. |
| A.5.12 | Classification of information | APPLICABLE | PARTIAL | Candidate PII identified in constraints ("never query the database"). No formal classification scheme. |
| A.5.13 | Labelling of information | APPLICABLE | NOT IMPLEMENTED | No data classification labels in systems. |
| A.5.14 | Information transfer | APPLICABLE | PARTIAL | HTTPS for all API traffic. No formal information transfer policy. |
| A.5.15 | Access control | APPLICABLE | IMPLEMENTED | JWT auth on all authenticated endpoints. Org isolation verified by tests. |
| A.5.16 | Identity management | APPLICABLE | IMPLEMENTED | Supabase Auth (ExecFlex), custom JWT (governance-platform, transparency-platform). |
| A.5.17 | Authentication information | APPLICABLE | PARTIAL | Passwords hashed (bcrypt). No MFA enforced. No password complexity policy. |
| A.5.18 | Access rights | APPLICABLE | IMPLEMENTED | Role-based (admin, owner, user). Org-scoped data access. |
| A.5.19 | Information security in supplier relationships | APPLICABLE | PARTIAL | Anthropic, Supabase, Stripe, Render, OpenAI as suppliers. No formal supplier security assessments. |
| A.5.20 | Addressing information security within supplier agreements | APPLICABLE | NOT IMPLEMENTED | Standard ToS accepted. No custom DPAs beyond defaults. |
| A.5.21 | Managing information security in the ICT supply chain | APPLICABLE | PARTIAL | Dependency pinning in requirements.txt. No formal supply chain risk assessment. |
| A.5.22 | Monitoring, review and change management of supplier services | APPLICABLE | NOT IMPLEMENTED | No formal supplier review cycle. |
| A.5.23 | Information security for use of cloud services | APPLICABLE | PARTIAL | Supabase (DB), Render (hosting), Vercel (frontends). No formal cloud security policy. |
| A.5.24 | Information security incident management planning and preparation | APPLICABLE | PARTIAL | Incident history documented (v0.16.1-16.4). No formal IRP. See iso/INCIDENT_RESPONSE.md. |
| A.5.25 | Assessment and decision on information security events | APPLICABLE | PARTIAL | Uptime monitors detect outages. No formal triage process. |
| A.5.26 | Response to information security incidents | APPLICABLE | PARTIAL | Ad-hoc response documented in commit history. No formal playbook. |
| A.5.27 | Learning from information security incidents | APPLICABLE | IMPLEMENTED | Post-incident commits document root cause (v0.16.1-16.4 series). |
| A.5.28 | Collection of evidence | APPLICABLE | PARTIAL | Git history as audit trail. No formal evidence collection procedure. |
| A.5.29 | Information security during disruption | APPLICABLE | PARTIAL | Single-box deployment is an SPOF. See PROD_CLEANUP.md #4. |
| A.5.30 | ICT readiness for business continuity | APPLICABLE | NOT IMPLEMENTED | No BCP. No documented RTO/RPO. |
| A.5.31 | Legal, statutory, regulatory and contractual requirements | APPLICABLE | PARTIAL | GDPR, EU AI Act, Pay Transparency Directive addressed in product design. No formal legal register. |
| A.5.32 | Intellectual property rights | APPLICABLE | IMPLEMENTED | All code proprietary. OSS dependencies tracked in requirements.txt/package.json. |
| A.5.33 | Protection of records | APPLICABLE | PARTIAL | Database backups via Supabase PiTR. No formal records retention policy. |
| A.5.34 | Privacy and protection of PII | APPLICABLE | PARTIAL | GDPR data rights endpoints. Privacy toggle on Cara. No formal DPIA. |
| A.5.35 | Independent review of information security | APPLICABLE | NOT IMPLEMENTED | No external audit performed. |
| A.5.36 | Compliance with policies, rules and standards for information security | APPLICABLE | PARTIAL | Automated security tests (12 in ExecFlex). No formal compliance verification cycle. |
| A.5.37 | Documented operating procedures | APPLICABLE | PARTIAL | CLAUDE.md + deploy scripts. No formal SOPs. |

## A.6 People Controls

| Control | Title | Applicability | Status | Justification |
|---------|-------|--------------|--------|---------------|
| A.6.1 | Screening | NOT APPLICABLE | — | Single developer/owner. No employee screening needed. |
| A.6.2 | Terms and conditions of employment | NOT APPLICABLE | — | No employees. |
| A.6.3 | Information security awareness, education and training | NOT APPLICABLE | — | Single developer with security expertise. |
| A.6.4 | Disciplinary process | NOT APPLICABLE | — | No employees. |
| A.6.5 | Responsibilities after termination or change of employment | NOT APPLICABLE | — | No employees. |
| A.6.6 | Confidentiality or non-disclosure agreements | APPLICABLE | NOT IMPLEMENTED | No NDAs with any party. |
| A.6.7 | Remote working | APPLICABLE | PARTIAL | All work is remote. No formal remote working policy. |
| A.6.8 | Information security event reporting | APPLICABLE | PARTIAL | GitHub issues + commit history. No formal reporting channel. |

## A.7 Physical Controls

| Control | Title | Applicability | Status | Justification |
|---------|-------|--------------|--------|---------------|
| A.7.1 | Physical security perimeters | NOT APPLICABLE | — | Cloud-only infrastructure. No physical premises. |
| A.7.2 | Physical entry | NOT APPLICABLE | — | Cloud-only. |
| A.7.3 | Securing offices, rooms and facilities | NOT APPLICABLE | — | Cloud-only. |
| A.7.4 | Physical security monitoring | NOT APPLICABLE | — | Cloud-only. |
| A.7.5 | Protecting against physical and environmental threats | NOT APPLICABLE | — | Cloud provider responsibility (Supabase, Render). |
| A.7.6 | Working in secure areas | NOT APPLICABLE | — | Cloud-only. |
| A.7.7 | Clear desk and clear screen | APPLICABLE | NOT IMPLEMENTED | No policy. Development on personal device. |
| A.7.8 | Equipment siting and protection | NOT APPLICABLE | — | Cloud-only. |
| A.7.9 | Security of assets off-premises | APPLICABLE | PARTIAL | Development laptop. No formal asset protection policy. |
| A.7.10 | Storage media | APPLICABLE | PARTIAL | Git repos on GitHub (encrypted at rest). Local dev on personal SSD. |
| A.7.11 | Supporting utilities | NOT APPLICABLE | — | Cloud provider responsibility. |
| A.7.12 | Cabling security | NOT APPLICABLE | — | Cloud-only. |
| A.7.13 | Equipment maintenance | NOT APPLICABLE | — | Cloud provider responsibility. |
| A.7.14 | Secure disposal or re-use of equipment | NOT APPLICABLE | — | No equipment disposal process needed. |

## A.8 Technological Controls

| Control | Title | Applicability | Status | Justification |
|---------|-------|--------------|--------|---------------|
| A.8.1 | User endpoint devices | APPLICABLE | PARTIAL | Development on personal Windows device. No MDM. |
| A.8.2 | Privileged access rights | APPLICABLE | PARTIAL | Admin roles in products. GitHub owner access. No PAM tool. |
| A.8.3 | Information access restriction | APPLICABLE | IMPLEMENTED | Org-scoped data access. JWT-based auth. |
| A.8.4 | Access to source code | APPLICABLE | IMPLEMENTED | Private GitHub repos. Owner-only write access. |
| A.8.5 | Secure authentication | APPLICABLE | PARTIAL | JWT + bcrypt. No MFA. |
| A.8.6 | Capacity management | APPLICABLE | NOT IMPLEMENTED | Single-box deployment. No capacity planning. |
| A.8.7 | Protection against malware | NOT APPLICABLE | — | Server-side Python/Node. No user-uploaded executable content. |
| A.8.8 | Management of technical vulnerabilities | APPLICABLE | PARTIAL | Dependabot. No formal vulnerability management process. |
| A.8.9 | Configuration management | APPLICABLE | PARTIAL | .env files + env vars. No formal config management tool. |
| A.8.10 | Information deletion | APPLICABLE | PARTIAL | GDPR data rights endpoints support deletion requests. |
| A.8.11 | Data masking | APPLICABLE | PARTIAL | PII sanitizer in governance-platform logs. No masking in other repos. |
| A.8.12 | Data leakage prevention | APPLICABLE | PARTIAL | .gitignore for .env files. CLAUDE.md constraints on database access. |
| A.8.13 | Information backup | APPLICABLE | PARTIAL | Supabase PiTR. No local backup scripts. See PROD_CLEANUP.md #5. |
| A.8.14 | Redundancy of information processing facilities | APPLICABLE | NOT IMPLEMENTED | Single-box. See PROD_CLEANUP.md #4. |
| A.8.15 | Logging | APPLICABLE | PARTIAL | Application logging. No centralised SIEM. Governance-platform now has structured logging. |
| A.8.16 | Monitoring activities | APPLICABLE | PARTIAL | Uptime monitors (Cara, ExecFlex). No centralised monitoring. |
| A.8.17 | Clock synchronisation | APPLICABLE | IMPLEMENTED | Cloud providers handle NTP. UTC timestamps in logs. |
| A.8.18 | Use of privileged utility programs | NOT APPLICABLE | — | No privileged system utilities in use. |
| A.8.19 | Installation of software on operational systems | APPLICABLE | PARTIAL | Deploy scripts. No formal change approval process. |
| A.8.20 | Networks security | APPLICABLE | PARTIAL | HTTPS everywhere. Cloud provider firewalls. No WAF. |
| A.8.21 | Security of network services | APPLICABLE | PARTIAL | Supabase managed networking. No formal network security review. |
| A.8.22 | Segregation of networks | NOT APPLICABLE | — | Cloud-managed networking. No internal network segmentation needed. |
| A.8.23 | Web filtering | NOT APPLICABLE | — | Server-side services only. |
| A.8.24 | Use of cryptography | APPLICABLE | IMPLEMENTED | HTTPS/TLS, bcrypt, JWT HS256. |
| A.8.25 | Secure development life cycle | APPLICABLE | PARTIAL | Automated tests (217+736+42 across estate). No formal SDLC policy. |
| A.8.26 | Application security requirements | APPLICABLE | PARTIAL | Security tests exist. No formal security requirements specification. |
| A.8.27 | Secure system architecture and engineering principles | APPLICABLE | PARTIAL | Org isolation by design. Feature flags. Fail-graceful AI. |
| A.8.28 | Secure coding | APPLICABLE | IMPLEMENTED | Parameterised queries. Input validation. Rate limiting. |
| A.8.29 | Security testing in development and acceptance | APPLICABLE | PARTIAL | 12 security verification tests. No penetration testing. |
| A.8.30 | Outsourced development | NOT APPLICABLE | — | All development in-house (AI-assisted). |
| A.8.31 | Separation of development, test and production environments | APPLICABLE | PARTIAL | .env-based config. Same codebase serves dev and prod. |
| A.8.32 | Change management | APPLICABLE | PARTIAL | Git branches + PR workflow. See iso/CHANGE_MANAGEMENT.md. |
| A.8.33 | Test information | APPLICABLE | IMPLEMENTED | Synthetic data only. Zero real data in tests (D-10). |
| A.8.34 | Protection of information systems during audit testing | APPLICABLE | PARTIAL | Read-only audit approach. CLAUDE.md constraints. |

---

## Summary

| Category | Total Controls | Applicable | Implemented | Partial | Not Implemented | Not Applicable |
|----------|---------------|------------|-------------|---------|-----------------|----------------|
| A.5 Organisational | 37 | 35 | 8 | 19 | 8 | 2 |
| A.6 People | 8 | 3 | 0 | 2 | 1 | 5 |
| A.7 Physical | 14 | 3 | 0 | 2 | 1 | 11 |
| A.8 Technological | 34 | 29 | 7 | 17 | 5 | 5 |
| **Total** | **93** | **70** | **15** | **40** | **15** | **23** |

**Certification gap:** 55 controls applicable but not fully implemented. Priority items in GAP_LIST.md.
