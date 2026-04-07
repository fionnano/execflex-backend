-- Inbound landing-page leads captured by POST /submit-brief
CREATE TABLE IF NOT EXISTS inbound_leads (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email       TEXT NOT NULL,
    name        TEXT,
    company     TEXT,
    message     TEXT,
    source      TEXT NOT NULL DEFAULT 'landing_page',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_inbound_leads_email      ON inbound_leads (email);
CREATE INDEX IF NOT EXISTS idx_inbound_leads_created_at ON inbound_leads (created_at DESC);
