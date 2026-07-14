-- ══════════════════════════════════════════════════════════════════════════
-- ainm Marketplace — dedicated schema (GRADUATION PATH, not yet applied to prod)
-- ══════════════════════════════════════════════════════════════════════════
--
-- STATUS: This migration is the clean, dedicated-table schema for the
-- marketplace. It is idempotent and ready to apply. As of the marketplace-mvp
-- ship it is NOT yet applied to production — the running MVP persists on the
-- existing durable tables under a namespace (see DECISIONS.md D-14), because
-- there is no autonomous DDL path to the prod Supabase. Apply this by pasting it
-- into the Supabase dashboard SQL editor when ready to graduate the marketplace
-- onto its own tables; then point services/marketplace/store.py at these tables.
--
-- Every statement is idempotent (IF NOT EXISTS / guarded enums).

-- 1. Leaders (supply side) ---------------------------------------------------
CREATE TABLE IF NOT EXISTS marketplace_leaders (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name              TEXT NOT NULL,
    headline          TEXT,
    bio               TEXT,
    location          TEXT,
    skills            JSONB NOT NULL DEFAULT '[]'::jsonb,
    sectors           JSONB NOT NULL DEFAULT '[]'::jsonb,
    seniority         TEXT,
    track             TEXT,
    engagement        TEXT DEFAULT 'both' CHECK (engagement IN ('fractional', 'permanent', 'both')),
    comp_expectation  TEXT,
    years_experience  INT DEFAULT 0,
    vetting_status    TEXT NOT NULL DEFAULT 'pending'
                      CHECK (vetting_status IN ('pending', 'in_progress', 'verified', 'rejected')),
    vetting_score     INT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_mkt_leaders_status ON marketplace_leaders(vetting_status);
CREATE INDEX IF NOT EXISTS idx_mkt_leaders_track ON marketplace_leaders(track);

-- 2. Vetting assessments (per leader) ----------------------------------------
CREATE TABLE IF NOT EXISTS marketplace_vetting_assessments (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    leader_id     UUID NOT NULL REFERENCES marketplace_leaders(id) ON DELETE CASCADE,
    track         TEXT NOT NULL,
    questions     JSONB NOT NULL DEFAULT '[]'::jsonb,
    responses     JSONB NOT NULL DEFAULT '[]'::jsonb,
    score         INT,
    passed        BOOLEAN,
    rationale     TEXT,
    per_competency JSONB DEFAULT '[]'::jsonb,
    model_used    TEXT,
    ai_generated  BOOLEAN DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_mkt_vetting_leader ON marketplace_vetting_assessments(leader_id);

-- 3. Companies (demand side) -------------------------------------------------
CREATE TABLE IF NOT EXISTS marketplace_companies (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    sector      TEXT,
    size        TEXT,
    location    TEXT,
    website     TEXT,
    org_id      UUID,   -- optional link to organizations
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 4. Opportunities (roles companies post) ------------------------------------
CREATE TABLE IF NOT EXISTS marketplace_opportunities (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id        UUID REFERENCES marketplace_companies(id) ON DELETE CASCADE,
    title             TEXT NOT NULL,
    description       TEXT,
    track             TEXT,
    sector            TEXT,
    commitment_type   TEXT,
    location          TEXT,
    is_remote         BOOLEAN DEFAULT TRUE,
    pay_range_min     NUMERIC(12, 2),
    pay_range_max     NUMERIC(12, 2),
    pay_range_currency TEXT DEFAULT 'EUR',
    status            TEXT NOT NULL DEFAULT 'open',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_mkt_opps_company ON marketplace_opportunities(company_id);

-- 5. Introductions (the billable event) --------------------------------------
CREATE TABLE IF NOT EXISTS marketplace_introductions (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id      UUID NOT NULL,   -- the requesting company's org (buyer)
    leader_id            UUID NOT NULL REFERENCES marketplace_leaders(id),
    company_id           UUID REFERENCES marketplace_companies(id),
    opportunity_id       UUID REFERENCES marketplace_opportunities(id),
    requested_by         UUID,
    status               TEXT NOT NULL DEFAULT 'requested'
                         CHECK (status IN ('requested', 'accepted', 'declined',
                                           'interviewing', 'hired', 'closed')),
    message              TEXT,
    first_year_comp      NUMERIC(12, 2),
    placement_fee_pct    NUMERIC(5, 2) NOT NULL DEFAULT 15.0,
    placement_fee_amount NUMERIC(12, 2),
    hired                BOOLEAN NOT NULL DEFAULT FALSE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_mkt_intros_org ON marketplace_introductions(organization_id);
CREATE INDEX IF NOT EXISTS idx_mkt_intros_leader ON marketplace_introductions(leader_id);
CREATE INDEX IF NOT EXISTS idx_mkt_intros_status ON marketplace_introductions(status);
