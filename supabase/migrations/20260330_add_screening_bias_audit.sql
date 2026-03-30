-- Screening bias audit table for EU AI Act compliance
-- Tracks fairness controls applied during each screening call
CREATE TABLE IF NOT EXISTS screening_bias_audit (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  interaction_id UUID NOT NULL,
  job_id UUID NOT NULL,
  role_id TEXT,
  role_title TEXT,
  company_name TEXT,
  -- Question consistency
  questions_asked INTEGER NOT NULL DEFAULT 0,
  questions_expected INTEGER NOT NULL DEFAULT 0,
  questions_skipped INTEGER NOT NULL DEFAULT 0,
  question_order_preserved BOOLEAN NOT NULL DEFAULT TRUE,
  -- Scoring metadata
  overall_score NUMERIC(3, 1),
  recommendation TEXT,
  score_std_deviation NUMERIC(4, 2),
  bias_flags JSONB DEFAULT '[]'::JSONB,
  -- AI disclosure and consent
  ai_disclosure_given BOOLEAN NOT NULL DEFAULT FALSE,
  candidate_consented BOOLEAN,
  -- Process metadata
  scoring_model TEXT DEFAULT 'gpt-4o',
  prompt_version TEXT NOT NULL DEFAULT 'v1_eu_ai_act',
  transcript_length INTEGER DEFAULT 0,
  call_duration_seconds INTEGER DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_screening_bias_audit_job_id ON screening_bias_audit (job_id);
CREATE INDEX IF NOT EXISTS idx_screening_bias_audit_role_id ON screening_bias_audit (role_id);
CREATE INDEX IF NOT EXISTS idx_screening_bias_audit_role_title ON screening_bias_audit (role_title);
CREATE INDEX IF NOT EXISTS idx_screening_bias_audit_created_at ON screening_bias_audit (created_at DESC);
