-- EU AI Act / GDPR consent capture on the candidate profile.
--
-- Aidan asks for verbal consent at the start of every screening call
-- and the bias-audit logger infers consent from the transcript keywords.
-- When consent is captured we flip consent_given on the candidate's
-- people_profiles row and stamp consent_given_at.
ALTER TABLE people_profiles
  ADD COLUMN IF NOT EXISTS consent_given BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE people_profiles
  ADD COLUMN IF NOT EXISTS consent_given_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_people_profiles_consent_given
  ON people_profiles (consent_given);
