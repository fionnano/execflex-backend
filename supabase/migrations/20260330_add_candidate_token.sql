-- Add candidate_token to interactions for candidate portal access
ALTER TABLE interactions
  ADD COLUMN IF NOT EXISTS candidate_token UUID UNIQUE;

CREATE INDEX IF NOT EXISTS idx_interactions_candidate_token
  ON interactions (candidate_token) WHERE candidate_token IS NOT NULL;
