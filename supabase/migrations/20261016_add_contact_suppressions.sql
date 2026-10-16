-- Global do-not-contact list (GDPR-E-004, Art. 21).
--
-- Rows are written when a candidate replies "not interested", when an
-- erasure request is completed, or when an admin records an objection.
-- services/compliance/suppression.py checks it before every outreach
-- e-mail and before any outbound call job is created.
--
-- Apply manually in Supabase SQL Editor (Primary database, branch main)
-- BEFORE merging the code that writes to it, then verify with a
-- service-key probe: GET /rest/v1/contact_suppressions?select=id&limit=1 -> 200.

CREATE TABLE IF NOT EXISTS contact_suppressions (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email       TEXT,
  phone       TEXT,
  reason      TEXT NOT NULL DEFAULT 'objection',
  source      TEXT,
  profile_id  UUID,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (email IS NOT NULL OR phone IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_contact_suppressions_email
  ON contact_suppressions (email);

CREATE INDEX IF NOT EXISTS idx_contact_suppressions_phone
  ON contact_suppressions (phone);

ALTER TABLE contact_suppressions ENABLE ROW LEVEL SECURITY;
-- Service-role access only (the backend uses the service key).
