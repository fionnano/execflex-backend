# Change Management Policy — DRAFT

**Organisation:** Ainm Technologies
**Date:** 2026-07-05
**Status:** DRAFT scaffold — derived from actual release practices
**Standard:** ISO 27001 A.8.32, ISO 42001 6.3

---

## 1. Scope

All changes to information systems, AI systems, infrastructure, and configuration across the ainm product estate.

## 2. Change Categories

| Category | Definition | Approval | Lead Time | Examples |
|----------|-----------|----------|-----------|---------|
| Standard | Pre-approved, low-risk, repeatable | None (pre-authorised) | Immediate | Dependency updates (patch), copy fixes, log level changes |
| Normal | Assessed risk, planned deployment | Owner review | 1 business day | New feature branch, model routing change, new endpoint |
| Emergency | Unplanned, responding to incident | Post-hoc review within 5 days | Immediate | Security patch, credential rotation, outage hotfix |

## 3. Change Process

### 3.1 Request

All changes originate as:
- Git branch from the appropriate base (main/master/rebuild-v1)
- Commit messages describe what and why
- Branch naming convention: `feature/*`, `fix/*`, `security-*`, `defect-*`, `overhaul-*`

### 3.2 Risk Assessment

Before merge, assess:
1. **Blast radius** — which products/users are affected?
2. **Reversibility** — can this be rolled back without data loss?
3. **Dependencies** — does this affect shared libraries (agentic-core)?
4. **Data impact** — does this change schema, PII handling, or data flows?
5. **AI behaviour** — does this change model selection, prompts, or scoring?

### 3.3 Multi-Consumer Gate (agentic-core changes)

Changes to `agentic-core` must pass ALL consumer test suites before release:
- transparency-platform: 231 tests (v0.16.4 pinned)
- ExecFlex: 217 tests (recruitment-agents branch)
- Compliance module: 42 tests (new)

**Decision D-35 formalises this gate.** No agentic-core release without all three passing.

### 3.4 Approval

| Change Type | Approver | Method |
|------------|----------|--------|
| Standard | Automated (tests pass) | CI/CD pipeline |
| Normal | Fionn (owner) | PR review or autonomous session review |
| Emergency | Fionn (owner) | Post-hoc commit review within 5 business days |

### 3.5 Implementation

1. Run full test suite for affected product
2. Deploy to staging if available (governance-platform, transparency-platform have staging)
3. Deploy to production
4. Monitor for 24 hours post-deploy

### 3.6 Post-Implementation Review

For Normal and Emergency changes:
- Verify deployment succeeded (health check, smoke test)
- Monitor error rates for 24 hours
- Update DECISIONS.md if architectural decisions were made
- Update KNOWN_DEFECTS.md if the change resolves a defect

## 4. Evidence of Current Practice

### Branch Strategy (actual)

| Repository | Main Branch | Feature Branches | Release Method |
|-----------|-------------|-----------------|----------------|
| execflex-backend | main | rebuild-v1, security-hardening | Render auto-deploy from main |
| execo-bridge | main | rebuild-v1 | Vercel auto-deploy from main |
| agentic-core | main | recruitment-agents | pip install from branch/tag |
| governance-platform | main | overhaul-2026-07, security-hardening | Docker deploy |
| transparency-platform | master | defect-fixes | Docker deploy |
| hr-advisory-agent | main | cara-privacy | Render auto-deploy |

### Test Coverage (current)

| Repository | Test Count | Coverage Areas |
|-----------|-----------|----------------|
| execflex-backend | 217 | Matching, screening, syndication, compliance, security, AI agents |
| transparency-platform | 231 | Pay equity, DFY pack, data provider, bias detection |
| agentic-core | 131 | Compliance module, recruitment agents, primitives |
| governance-platform | 42 | Snapshot scoring, prohibited practices, rate limiter, sanitizer |
| hr-advisory-agent | 0 | No automated tests (GAP) |

### Autonomous Session Practice

Large-scale changes are executed via time-boxed autonomous Claude Code sessions with:
- Hard constraints documented before session starts
- DECISIONS.md updated for every architectural choice
- Branch-per-change, never direct to main
- SUMMARY.md with confidence-ranked decisions for owner review

## 5. AI-Specific Change Controls (ISO 42001)

Changes affecting AI systems require additional assessment:

| Change Type | Additional Requirement |
|-------------|----------------------|
| Model version change | Test on representative dataset. Log old vs new model performance. |
| Prompt modification | Document intent. Verify no prompt injection vectors introduced. |
| Scoring weight change | Re-run test suite. Compare distribution of outcomes. |
| New AI capability | Feature flag (OFF by default). Document intended purpose. |
| Training data change | Not applicable (no fine-tuned models). |

---

## Maintenance

Review this policy:
- When team size changes
- When deployment infrastructure changes
- Annually as part of ISMS review
