-- ══════════════════════════════════════════════════════════════════════════
-- ainm AI Act Check — dedicated table + RLS (THE SINGLE HUMAN STEP)
-- ══════════════════════════════════════════════════════════════════════════
--
-- HOW TO APPLY: paste this whole file into the Supabase dashboard SQL editor
-- (project krzacydualjpsapffpfm) and run it. Idempotent — safe to re-run.
--
-- WHY OPTIONAL FOR LAUNCH: the AI Act Check runs today WITHOUT this migration.
-- Assessments persist on the existing durable activity_log table under a
-- namespace (DECISIONS.md D-18): entity_type='client',
-- activity_type='ai_act_assessment', metadata.aiact=true, owned by the creating
-- org. Every read is org-scoped in code. There is no autonomous DDL path to prod
-- Supabase (no DB password / management token), so this file is generated for a
-- human to apply when ready to graduate onto a dedicated table. After applying,
-- repoint services/aiact/store.py at this table — the response shapes already
-- match, so no route changes are needed.

-- ─────────────────────────────────────────────────────────────────────────────
-- Dedicated assessments table
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS aiact_assessments (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id      UUID NOT NULL,              -- owning org (tenant key)
    created_by           UUID,                       -- auth.users id
    system_name          TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'draft'
                         CHECK (status IN ('draft', 'scored')),
    answers              JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- The full engine result (risk_classification, readiness_score, decision,
    -- summary, prohibited, obligations, gaps, recommendations, articles,
    -- ai_generated, model_used).
    result               JSONB,
    risk_classification  TEXT,                       -- denormalised for filtering
    readiness_score      INT,
    ai_generated         BOOLEAN NOT NULL DEFAULT FALSE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_aiact_org ON aiact_assessments(organization_id);
CREATE INDEX IF NOT EXISTS idx_aiact_status ON aiact_assessments(status);
CREATE INDEX IF NOT EXISTS idx_aiact_risk ON aiact_assessments(risk_classification);

-- ═════════════════════════════════════════════════════════════════════════════
-- Row-Level Security — a company sees only its own assessments
-- ═════════════════════════════════════════════════════════════════════════════
-- The Flask backend uses the service role (bypasses RLS) and already enforces
-- org scoping in code. These policies are defence-in-depth for any direct/anon
-- access and for a future client talking to PostgREST with a user JWT.
-- org_id is read from the JWT app_metadata claim (provisioned by handle_new_user).

ALTER TABLE aiact_assessments ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS aiact_tenant ON aiact_assessments;
CREATE POLICY aiact_tenant ON aiact_assessments
    FOR ALL
    USING (organization_id = (auth.jwt() -> 'app_metadata' ->> 'org_id')::uuid)
    WITH CHECK (organization_id = (auth.jwt() -> 'app_metadata' ->> 'org_id')::uuid);

-- ─────────────────────────────────────────────────────────────────────────────
-- Done. After applying: repoint services/aiact/store.py at aiact_assessments.
-- ─────────────────────────────────────────────────────────────────────────────
