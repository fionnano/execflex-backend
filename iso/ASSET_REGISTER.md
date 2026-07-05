# Asset Register — ISO 27001 / ISO 42001

**Source:** ESTATE_MAP.md + CANONICAL_AGENT_COUNT.md
**Date:** 2026-07-05
**Status:** DRAFT scaffold

---

## 1. Information Systems

| Asset ID | Name | Type | Owner | Location | Classification | Notes |
|----------|------|------|-------|----------|----------------|-------|
| IS-001 | execflex-backend | Web API | Fionn | Render (US) | Confidential | Flask, Supabase PG, Python |
| IS-002 | execo-bridge | Web Frontend | Fionn | Vercel | Internal | React 18, Vite, TypeScript |
| IS-003 | agentic-core | Shared Library | Fionn | GitHub (private) | Confidential | Python, pip-installable |
| IS-004 | governance-platform | Web Application | Fionn | compliance.ainm.ai | Confidential | FastAPI, SQLAlchemy, React |
| IS-005 | transparency-platform | Web Application | Fionn | transparency.ainm.ai | Confidential | FastAPI, SQLAlchemy, React |
| IS-006 | hr-advisory-agent | Web Application | Fionn | ainm.ai | Confidential | FastAPI, Supabase, React |

## 2. Data Stores

| Asset ID | Name | Type | Owner | Location | Classification | PII | Notes |
|----------|------|------|-------|----------|----------------|-----|-------|
| DS-001 | ExecFlex PostgreSQL | Database | Fionn | Supabase (krzacydu...) | Highly Confidential | Yes | Candidate data, screening results |
| DS-002 | governance-platform PostgreSQL | Database | Fionn | Docker/managed | Confidential | Yes | User accounts, assessment data |
| DS-003 | transparency-platform PostgreSQL | Database | Fionn | Docker/managed | Highly Confidential | Yes | Employee pay records (append-only) |
| DS-004 | ainm PostgreSQL | Database | Fionn | Supabase | Highly Confidential | Yes | HR conversations, employee data |
| DS-005 | governance-platform ChromaDB | Vector store | Fionn | Docker volume | Confidential | Possible | Document embeddings for RAG |

## 3. AI Systems (per CANONICAL_AGENT_COUNT.md)

| Asset ID | System | Agent Count | Model Provider | Tier | PII Processing |
|----------|--------|------------|----------------|------|----------------|
| AI-001 | agentic-core DFY agents | 11 | Anthropic (Sonnet) | REASONING | Yes (pay data) |
| AI-002 | ExecFlex recruitment agents | 10 | Anthropic (Sonnet/Haiku) + OpenAI Realtime | Mixed | Yes (candidate data) |
| AI-003 | transparency-platform features | 6 | Anthropic (Sonnet/Haiku) | Mixed | Yes (pay/employee data) |
| AI-004 | hr-advisory-agent (ainm) | 30 | Anthropic (Sonnet/Haiku) + OpenAI (images) | Mixed | Yes (HR conversations) |
| AI-005 | governance-platform AI service | 7 functions | Anthropic (Haiku/Sonnet) | Mixed | No (assessment data only) |
| **Total** | | **57 + 7** | | | |

## 4. Third-Party Services

| Asset ID | Service | Provider | Purpose | Data Shared | Classification |
|----------|---------|----------|---------|-------------|----------------|
| TP-001 | Supabase | Supabase Inc | Database + Auth | All PII | Critical |
| TP-002 | Anthropic API | Anthropic | LLM inference | Prompts (may contain PII) | Critical |
| TP-003 | OpenAI API | OpenAI | Voice + images | Voice audio, prompts | Critical |
| TP-004 | Stripe | Stripe Inc | Billing | Customer email, payment | High |
| TP-005 | Render | Render Inc | Hosting | Application code, env vars | High |
| TP-006 | GitHub | Microsoft/GitHub | Source code | Proprietary code | High |
| TP-007 | Twilio | Twilio Inc | Voice telephony | Call audio, phone numbers | Critical |
| TP-008 | Resend | Resend Inc | Email delivery | Email addresses, content | Medium |
| TP-009 | Apollo | Apollo.io | Lead sourcing | Business contact data | Medium |
| TP-010 | Vercel | Vercel Inc | Frontend hosting | Static assets | Low |

## 5. Credentials & Secrets

| Asset ID | Secret | Location | Rotation | Last Rotated | Notes |
|----------|--------|----------|----------|-------------|-------|
| CR-001 | ANTHROPIC_API_KEY | .env (all repos) | UNKNOWN | UNKNOWN | REQUIRES_HUMAN: establish rotation |
| CR-002 | SUPABASE_SERVICE_KEY | .env (execflex, ainm) | UNKNOWN | UNKNOWN | CRITICAL: exposed in git history |
| CR-003 | SUPABASE_JWT_SECRET | .env (execflex) | UNKNOWN | UNKNOWN | Required for JWT verification |
| CR-004 | STRIPE_SECRET_KEY | .env (gov, trans, ainm, execflex) | UNKNOWN | UNKNOWN | Per-product keys |
| CR-005 | OPENAI_API_KEY | .env (execflex, ainm) | UNKNOWN | UNKNOWN | Voice + image generation |
| CR-006 | AINM_SERVICE_KEY | .env (execflex, ainm) | UNKNOWN | UNKNOWN | Service-to-service auth |
| CR-007 | EMAIL_PASS | .env (execflex) | UNKNOWN | UNKNOWN | Gmail app password |
| CR-008 | APOLLO_API_KEY | .env (execflex) | UNKNOWN | UNKNOWN | Lead sourcing |

---

## Maintenance

This register should be reviewed quarterly and updated whenever:
- A new codebase or service is added
- A new third-party integration is introduced
- AI agent count changes (re-run CANONICAL_AGENT_COUNT.md methodology)
- Credential rotation occurs
