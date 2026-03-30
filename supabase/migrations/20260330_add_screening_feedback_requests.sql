-- Screening feedback requests table for EU AI Act Article 86 compliance
-- Tracks candidate requests for explanation of screening outcomes
CREATE TABLE IF NOT EXISTS screening_feedback_requests (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  candidate_email TEXT NOT NULL,
  candidate_phone TEXT,
  interaction_id UUID,
  job_id UUID,
  role_id TEXT,
  role_title TEXT,
  requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  sent_at TIMESTAMPTZ,
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'sent', 'failed', 'no_data')),
  error_message TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_feedback_requests_email ON screening_feedback_requests (candidate_email);
CREATE INDEX IF NOT EXISTS idx_feedback_requests_interaction ON screening_feedback_requests (interaction_id);
CREATE INDEX IF NOT EXISTS idx_feedback_requests_created ON screening_feedback_requests (created_at DESC);
