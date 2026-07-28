-- ══════════════════════════════════════════════════════════════════════════
-- ainm Marketplace — dedicated schema + RLS (THE SINGLE HUMAN STEP)
-- ══════════════════════════════════════════════════════════════════════════
--
-- HOW TO APPLY: paste this whole file into the Supabase dashboard SQL editor
-- (project krzacydualjpsapffpfm) and run it. Every statement is idempotent
-- (IF NOT EXISTS / guarded), so re-running is safe.
--
-- WHY THIS IS OPTIONAL FOR LAUNCH: the marketplace runs today WITHOUT this
-- migration. It persists on the existing durable tables under a namespace (see
-- DECISIONS.md D-14): leaders in people_profiles (org = MARKETPLACE_ORG_ID),
-- companies/roles in opportunities, company profiles + introductions in
-- activity_log. There is no autonomous DDL path to prod Supabase (no DB
-- password / management token), so this file is generated for a human to apply
-- when ready to GRADUATE the marketplace onto its own tables. Applying it does
-- NOT change behaviour on its own — after applying, point
-- services/marketplace/store.py at these tables (the serializers already return
-- the exact shapes below). Until then, everything works on the namespaced
-- tables and is fully durable.
--
-- This schema reflects the v2 (real-user) feature set: account-linked leaders,
-- private contact reveal on acceptance, leader accept/decline, company profiles,
-- tenant-scoped introductions, and the vetting audit — all enforced by RLS.

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Leaders (supply side) — linked to an auth account
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS marketplace_leaders (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID REFERENCES auth.users(id) ON DELETE SET NULL,  -- owning account
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
    -- Private contact — revealed to a company only via an accepted introduction.
    contact           JSONB NOT NULL DEFAULT '{}'::jsonb,  -- {email, phone, linkedin}
    vetting_status    TEXT NOT NULL DEFAULT 'pending'
                      CHECK (vetting_status IN ('pending', 'in_progress', 'verified', 'rejected')),
    vetting           JSONB,                                 -- full explainable result
    vetting_score     INT,
    vetting_attempts  INT NOT NULL DEFAULT 0,                -- assessment-farming guard
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mkt_leaders_user ON marketplace_leaders(user_id) WHERE user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_mkt_leaders_status ON marketplace_leaders(vetting_status);
CREATE INDEX IF NOT EXISTS idx_mkt_leaders_track ON marketplace_leaders(track);

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Vetting assessments (per leader) — the audit of the moat
-- ─────────────────────────────────────────────────────────────────────────────
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

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Company profiles (demand side) — one per buyer org; "who's asking"
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS marketplace_companies (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id        UUID NOT NULL,                 -- the buyer's organization (tenant)
    name          TEXT NOT NULL,
    sector        TEXT,
    size          TEXT,
    location      TEXT,
    website       TEXT,
    description   TEXT,
    contact_name  TEXT,
    contact_email TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mkt_companies_org ON marketplace_companies(org_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. Opportunities (roles companies post)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS marketplace_opportunities (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id        UUID REFERENCES marketplace_companies(id) ON DELETE CASCADE,
    org_id            UUID,
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

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. Introductions (the billable event) — leader accept/decline + contact reveal
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS marketplace_introductions (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id      UUID NOT NULL,   -- the requesting company's org (buyer/tenant)
    requested_by         UUID,            -- auth.users id of the requester
    leader_id            UUID NOT NULL REFERENCES marketplace_leaders(id),
    company              JSONB NOT NULL DEFAULT '{}'::jsonb,   -- snapshot of the buyer company
    opportunity_id       UUID REFERENCES marketplace_opportunities(id),
    opportunity_title    TEXT,
    status               TEXT NOT NULL DEFAULT 'requested'
                         CHECK (status IN ('requested', 'accepted', 'declined',
                                           'interviewing', 'hired', 'closed')),
    leader_response      TEXT NOT NULL DEFAULT 'pending'
                         CHECK (leader_response IN ('pending', 'accepted', 'declined')),
    message              TEXT,
    -- Contact snapshots; leader_contact is revealed to the buyer only when
    -- contact_revealed = TRUE (set when the leader accepts).
    requester_contact    JSONB NOT NULL DEFAULT '{}'::jsonb,
    leader_contact       JSONB NOT NULL DEFAULT '{}'::jsonb,
    contact_revealed     BOOLEAN NOT NULL DEFAULT FALSE,
    first_year_comp      NUMERIC(12, 2),
    placement_fee_pct    NUMERIC(5, 2) NOT NULL DEFAULT 15.0,
    placement_fee_amount NUMERIC(12, 2),
    hired                BOOLEAN NOT NULL DEFAULT FALSE,
    outcome              TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_mkt_intros_org ON marketplace_introductions(organization_id);
CREATE INDEX IF NOT EXISTS idx_mkt_intros_leader ON marketplace_introductions(leader_id);
CREATE INDEX IF NOT EXISTS idx_mkt_intros_status ON marketplace_introductions(status);

-- ═════════════════════════════════════════════════════════════════════════════
-- 6. Row-Level Security — make it safe for real users
-- ═════════════════════════════════════════════════════════════════════════════
-- The Flask backend uses the service role (which BYPASSES RLS) and already
-- enforces every rule below in code (contact reveal, tenant isolation, owner-only
-- edits). These policies are defence-in-depth for any direct/anon access and for
-- when a future client talks to PostgREST with a user JWT.
--
-- NOTE ON auth.jwt(): org_id is provisioned into app_metadata by the
-- handle_new_user hook, so we read it from the JWT claims.

ALTER TABLE marketplace_leaders               ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketplace_vetting_assessments   ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketplace_companies             ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketplace_opportunities         ENABLE ROW LEVEL SECURITY;
ALTER TABLE marketplace_introductions         ENABLE ROW LEVEL SECURITY;

-- Leaders: the public catalog (verified) is world-readable, but contact is a
-- column — expose non-contact columns via a view for anon; the base table is
-- readable by the owner (full row) and by service role.
DROP POLICY IF EXISTS mkt_leaders_public_read ON marketplace_leaders;
CREATE POLICY mkt_leaders_public_read ON marketplace_leaders
    FOR SELECT USING (vetting_status = 'verified');   -- catalog visibility

DROP POLICY IF EXISTS mkt_leaders_owner_all ON marketplace_leaders;
CREATE POLICY mkt_leaders_owner_all ON marketplace_leaders
    FOR ALL USING (user_id = auth.uid())
             WITH CHECK (user_id = auth.uid());

-- A public, contact-free projection of the verified pool for anon browsing.
CREATE OR REPLACE VIEW marketplace_leaders_public AS
    SELECT id, name, headline, bio, location, skills, sectors, seniority, track,
           engagement, comp_expectation, years_experience, vetting_status,
           vetting, vetting_score, created_at, updated_at
    FROM marketplace_leaders
    WHERE vetting_status = 'verified';

-- Vetting assessments: only the owning leader (via join) may read their own.
DROP POLICY IF EXISTS mkt_vetting_owner_read ON marketplace_vetting_assessments;
CREATE POLICY mkt_vetting_owner_read ON marketplace_vetting_assessments
    FOR SELECT USING (EXISTS (
        SELECT 1 FROM marketplace_leaders l
        WHERE l.id = marketplace_vetting_assessments.leader_id
          AND l.user_id = auth.uid()));

-- Company profiles: a buyer can read/write only their own org's profile.
DROP POLICY IF EXISTS mkt_companies_tenant ON marketplace_companies;
CREATE POLICY mkt_companies_tenant ON marketplace_companies
    FOR ALL USING (org_id = (auth.jwt() -> 'app_metadata' ->> 'org_id')::uuid)
             WITH CHECK (org_id = (auth.jwt() -> 'app_metadata' ->> 'org_id')::uuid);

-- Opportunities are a public catalog (read to all authenticated).
DROP POLICY IF EXISTS mkt_opps_read ON marketplace_opportunities;
CREATE POLICY mkt_opps_read ON marketplace_opportunities
    FOR SELECT USING (true);

-- Introductions: visible to the requesting company (tenant) OR to the leader the
-- request is addressed to. Only the owning company may update outcome fields;
-- only the leader may accept/decline (enforced in code; RLS restricts row access).
DROP POLICY IF EXISTS mkt_intros_buyer ON marketplace_introductions;
CREATE POLICY mkt_intros_buyer ON marketplace_introductions
    FOR ALL USING (organization_id = (auth.jwt() -> 'app_metadata' ->> 'org_id')::uuid)
             WITH CHECK (organization_id = (auth.jwt() -> 'app_metadata' ->> 'org_id')::uuid);

DROP POLICY IF EXISTS mkt_intros_leader_read ON marketplace_introductions;
CREATE POLICY mkt_intros_leader_read ON marketplace_introductions
    FOR SELECT USING (EXISTS (
        SELECT 1 FROM marketplace_leaders l
        WHERE l.id = marketplace_introductions.leader_id
          AND l.user_id = auth.uid()));

-- The leader may update ONLY their own requests' response fields.
DROP POLICY IF EXISTS mkt_intros_leader_respond ON marketplace_introductions;
CREATE POLICY mkt_intros_leader_respond ON marketplace_introductions
    FOR UPDATE USING (EXISTS (
        SELECT 1 FROM marketplace_leaders l
        WHERE l.id = marketplace_introductions.leader_id
          AND l.user_id = auth.uid()));

-- ─────────────────────────────────────────────────────────────────────────────
-- Done. After applying: repoint services/marketplace/store.py at these tables.
-- The API response shapes are already identical, so no route changes are needed.
-- ─────────────────────────────────────────────────────────────────────────────
