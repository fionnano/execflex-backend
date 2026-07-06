-- rebuild-v1 schema migration
-- Multi-tenant, pipeline, compliance, syndication, talent pools
-- Applies on top of the existing 23-table schema + 10 incremental migrations.
-- All new tables include organization_id for multi-tenancy.
-- Security findings S-001/S-002/S-003 are addressed by design:
--   S-001: no debug endpoints in new routes
--   S-002: subscription check is middleware, not per-route bypass
--   S-003: all queries use parameterized SDK methods

-- ══════════════════════════════════════════════════════════════════
-- 1. USER ROLES (replaces hardcoded admin email checks)
-- ══════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS user_roles (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL,
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL DEFAULT 'recruiter'
                    CHECK (role IN ('owner', 'recruiter', 'viewer')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_id, organization_id)
);

CREATE INDEX IF NOT EXISTS idx_user_roles_user_id ON user_roles(user_id);
CREATE INDEX IF NOT EXISTS idx_user_roles_org_id ON user_roles(organization_id);

-- ══════════════════════════════════════════════════════════════════
-- 2. PIPELINE STAGES on people_profiles
-- ══════════════════════════════════════════════════════════════════

DO $$ BEGIN
    CREATE TYPE pipeline_stage AS ENUM (
        'sourced', 'screened', 'shortlisted', 'interviewing',
        'offered', 'placed', 'rejected', 'withdrawn'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

ALTER TABLE people_profiles
    ADD COLUMN IF NOT EXISTS pipeline_stage pipeline_stage DEFAULT 'sourced';
ALTER TABLE people_profiles
    ADD COLUMN IF NOT EXISTS stage_changed_at TIMESTAMPTZ;
ALTER TABLE people_profiles
    ADD COLUMN IF NOT EXISTS stage_changed_by UUID;

CREATE INDEX IF NOT EXISTS idx_people_profiles_pipeline_stage
    ON people_profiles(pipeline_stage);

-- ══════════════════════════════════════════════════════════════════
-- 3. PIPELINE EVENTS (transition log)
-- ══════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS pipeline_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id),
    candidate_id    UUID NOT NULL,
    opportunity_id  UUID REFERENCES opportunities(id),
    from_stage      pipeline_stage,
    to_stage        pipeline_stage NOT NULL,
    changed_by      UUID,
    reason          TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pipeline_events_org_id ON pipeline_events(organization_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_events_candidate_id ON pipeline_events(candidate_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_events_opportunity_id ON pipeline_events(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_events_created_at ON pipeline_events(created_at DESC);

-- ══════════════════════════════════════════════════════════════════
-- 4. ACTIVITY LOG (CRM activity feed)
-- ══════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS activity_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id),
    entity_type     TEXT NOT NULL CHECK (entity_type IN ('candidate', 'client', 'job', 'placement')),
    entity_id       UUID NOT NULL,
    activity_type   TEXT NOT NULL,
    actor_id        UUID,
    summary         TEXT,
    metadata        JSONB DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_activity_log_org_id ON activity_log(organization_id);
CREATE INDEX IF NOT EXISTS idx_activity_log_entity ON activity_log(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_activity_log_created_at ON activity_log(created_at DESC);

-- ══════════════════════════════════════════════════════════════════
-- 5. SCREENING SESSIONS (persisted state machine sessions)
-- ══════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS screening_sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id),
    session_type    TEXT NOT NULL CHECK (session_type IN ('candidate', 'client')),
    candidate_id    UUID,
    client_id       UUID,
    opportunity_id  UUID REFERENCES opportunities(id),
    state           TEXT NOT NULL DEFAULT 'idle',
    consent_given   BOOLEAN NOT NULL DEFAULT FALSE,
    questions       JSONB NOT NULL DEFAULT '[]'::jsonb,
    answers         JSONB NOT NULL DEFAULT '[]'::jsonb,
    outcome         JSONB,
    brief           JSONB,
    handoff_reason  TEXT,
    transitions     JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata        JSONB DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_screening_sessions_org_id ON screening_sessions(organization_id);
CREATE INDEX IF NOT EXISTS idx_screening_sessions_candidate ON screening_sessions(candidate_id);
CREATE INDEX IF NOT EXISTS idx_screening_sessions_state ON screening_sessions(state);

-- ══════════════════════════════════════════════════════════════════
-- 6. AI DECISION LOG (EU AI Act compliance — Art. 13 transparency)
-- ══════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS ai_decision_log (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID NOT NULL REFERENCES organizations(id),
    decision_type       TEXT NOT NULL CHECK (decision_type IN (
        'screening_score', 'match_rank', 'stage_change',
        'reject', 'shortlist', 'auto_match'
    )),
    candidate_id        UUID,
    opportunity_id      UUID REFERENCES opportunities(id),
    inputs              JSONB NOT NULL DEFAULT '{}'::jsonb,
    model_used          TEXT,
    model_version       TEXT,
    score               NUMERIC(6, 2),
    explanation         TEXT,
    dimension_scores    JSONB,
    human_reviewed      BOOLEAN NOT NULL DEFAULT FALSE,
    human_reviewer_id   UUID,
    human_review_at     TIMESTAMPTZ,
    human_override      BOOLEAN NOT NULL DEFAULT FALSE,
    override_reason     TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ai_decision_log_org_id ON ai_decision_log(organization_id);
CREATE INDEX IF NOT EXISTS idx_ai_decision_log_candidate ON ai_decision_log(candidate_id);
CREATE INDEX IF NOT EXISTS idx_ai_decision_log_type ON ai_decision_log(decision_type);
CREATE INDEX IF NOT EXISTS idx_ai_decision_log_created_at ON ai_decision_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_decision_log_not_reviewed
    ON ai_decision_log(organization_id) WHERE NOT human_reviewed;

-- ══════════════════════════════════════════════════════════════════
-- 7. DATA RIGHTS REQUESTS (GDPR Art. 15/17 — access/erasure)
-- ══════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS data_rights_requests (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id),
    request_type    TEXT NOT NULL CHECK (request_type IN ('access', 'erasure', 'rectification', 'portability')),
    requester_email TEXT NOT NULL,
    requester_name  TEXT,
    candidate_id    UUID,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'in_progress', 'completed', 'rejected')),
    notes           TEXT,
    completed_at    TIMESTAMPTZ,
    completed_by    UUID,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_data_rights_org_id ON data_rights_requests(organization_id);
CREATE INDEX IF NOT EXISTS idx_data_rights_status ON data_rights_requests(status);
CREATE INDEX IF NOT EXISTS idx_data_rights_email ON data_rights_requests(requester_email);

-- ══════════════════════════════════════════════════════════════════
-- 8. JOB SYNDICATION (multi-board posting)
-- ══════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS job_syndication (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id),
    opportunity_id  UUID NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
    board           TEXT NOT NULL,
    feed_format     TEXT NOT NULL DEFAULT 'xml',
    external_id     TEXT,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'submitted', 'live', 'expired', 'failed', 'removed')),
    submitted_at    TIMESTAMPTZ,
    live_at         TIMESTAMPTZ,
    error_message   TEXT,
    metadata        JSONB DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_job_syndication_org_id ON job_syndication(organization_id);
CREATE INDEX IF NOT EXISTS idx_job_syndication_opportunity ON job_syndication(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_job_syndication_board ON job_syndication(board);
CREATE INDEX IF NOT EXISTS idx_job_syndication_status ON job_syndication(status);

-- ══════════════════════════════════════════════════════════════════
-- 9. TALENT POOLS (ExecFlex Verified)
-- ══════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS talent_pools (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id),
    name            TEXT NOT NULL,
    description     TEXT,
    criteria        JSONB DEFAULT '{}'::jsonb,
    is_verified     BOOLEAN NOT NULL DEFAULT FALSE,
    verification_method TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_talent_pools_org_id ON talent_pools(organization_id);

CREATE TABLE IF NOT EXISTS talent_pool_members (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pool_id         UUID NOT NULL REFERENCES talent_pools(id) ON DELETE CASCADE,
    candidate_id    UUID NOT NULL,
    verified        BOOLEAN NOT NULL DEFAULT FALSE,
    verified_at     TIMESTAMPTZ,
    assessment_provider TEXT,
    assessment_score NUMERIC(5, 2),
    assessment_data JSONB DEFAULT '{}'::jsonb,
    added_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(pool_id, candidate_id)
);

CREATE INDEX IF NOT EXISTS idx_talent_pool_members_pool ON talent_pool_members(pool_id);
CREATE INDEX IF NOT EXISTS idx_talent_pool_members_candidate ON talent_pool_members(candidate_id);

-- ══════════════════════════════════════════════════════════════════
-- 10. PAY RANGE on opportunities (Pay Transparency Directive)
-- ══════════════════════════════════════════════════════════════════

ALTER TABLE opportunities
    ADD COLUMN IF NOT EXISTS pay_range_min NUMERIC(12, 2);
ALTER TABLE opportunities
    ADD COLUMN IF NOT EXISTS pay_range_max NUMERIC(12, 2);
ALTER TABLE opportunities
    ADD COLUMN IF NOT EXISTS pay_range_currency TEXT DEFAULT 'EUR';
ALTER TABLE opportunities
    ADD COLUMN IF NOT EXISTS pay_range_period TEXT DEFAULT 'annual'
        CHECK (pay_range_period IS NULL OR pay_range_period IN ('annual', 'monthly', 'daily', 'hourly'));

-- ══════════════════════════════════════════════════════════════════
-- 11. ADD organization_id TO EXISTING TABLES (multi-tenancy)
-- ══════════════════════════════════════════════════════════════════

ALTER TABLE people_profiles
    ADD COLUMN IF NOT EXISTS organization_id UUID REFERENCES organizations(id);
ALTER TABLE opportunities
    ADD COLUMN IF NOT EXISTS organization_id UUID REFERENCES organizations(id);
ALTER TABLE interactions
    ADD COLUMN IF NOT EXISTS organization_id UUID REFERENCES organizations(id);
ALTER TABLE client_contacts
    ADD COLUMN IF NOT EXISTS organization_id UUID REFERENCES organizations(id);

CREATE INDEX IF NOT EXISTS idx_people_profiles_org_id ON people_profiles(organization_id);
CREATE INDEX IF NOT EXISTS idx_opportunities_org_id ON opportunities(organization_id);
CREATE INDEX IF NOT EXISTS idx_interactions_org_id ON interactions(organization_id);
CREATE INDEX IF NOT EXISTS idx_client_contacts_org_id ON client_contacts(organization_id);

-- ══════════════════════════════════════════════════════════════════
-- 12. APPLICATIONS (explicit candidate→job link with status)
-- ══════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS applications (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id),
    candidate_id    UUID NOT NULL,
    opportunity_id  UUID NOT NULL REFERENCES opportunities(id),
    status          TEXT NOT NULL DEFAULT 'applied'
                    CHECK (status IN ('applied', 'screening', 'screened',
                                      'shortlisted', 'interviewing', 'offered',
                                      'placed', 'rejected', 'withdrawn')),
    source          TEXT DEFAULT 'direct',
    screening_session_id UUID REFERENCES screening_sessions(id),
    match_score     NUMERIC(5, 2),
    match_explanation TEXT,
    human_reviewed  BOOLEAN NOT NULL DEFAULT FALSE,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(candidate_id, opportunity_id)
);

CREATE INDEX IF NOT EXISTS idx_applications_org_id ON applications(organization_id);
CREATE INDEX IF NOT EXISTS idx_applications_candidate ON applications(candidate_id);
CREATE INDEX IF NOT EXISTS idx_applications_opportunity ON applications(opportunity_id);
CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);
