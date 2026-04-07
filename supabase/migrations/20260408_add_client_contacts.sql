-- client_contacts: company-side decision-makers uploaded via
-- POST /admin/upload/clients. Separate from people_profiles
-- because these are hirers/buyers, not candidates.
CREATE TABLE IF NOT EXISTS client_contacts (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name             TEXT,
    title            TEXT,
    company          TEXT,
    email            TEXT,
    work_phone       TEXT,
    mobile           TEXT,
    source           TEXT NOT NULL DEFAULT 'manual',
    source_metadata  JSONB NOT NULL DEFAULT '{}'::jsonb,
    outreach_status  TEXT NOT NULL DEFAULT 'not_contacted',
    notes            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_client_contacts_email           ON client_contacts (email);
CREATE INDEX IF NOT EXISTS idx_client_contacts_company         ON client_contacts (company);
CREATE INDEX IF NOT EXISTS idx_client_contacts_outreach_status ON client_contacts (outreach_status);
CREATE INDEX IF NOT EXISTS idx_client_contacts_source          ON client_contacts (source);
