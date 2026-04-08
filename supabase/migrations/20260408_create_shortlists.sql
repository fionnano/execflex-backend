-- Shortlists: shareable, public-link candidate packs that employers
-- send to their clients. Each shortlist is a frozen snapshot of the
-- selected candidates at the time of creation (candidate_ids +
-- denormalised role_title/company_name for display).
--
-- The public shortlist page at /shortlist/<id> reads this row by
-- primary key — no auth required — but the row is intentionally
-- unguessable (uuid) and has an expires_at enforced in application
-- code (not RLS) so we can show a friendly "link expired" page
-- instead of a 404.
--
-- Apply manually in Supabase SQL Editor.

CREATE TABLE IF NOT EXISTS shortlists (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  opportunity_id     UUID NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
  created_by_user_id UUID NOT NULL,
  candidate_ids      UUID[] NOT NULL,
  message            TEXT,
  role_title         TEXT,
  company_name       TEXT,
  expires_at         TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '30 days'),
  viewed_count       INT NOT NULL DEFAULT 0,
  last_viewed_at     TIMESTAMPTZ,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_shortlists_opportunity_id
  ON shortlists(opportunity_id);

CREATE INDEX IF NOT EXISTS idx_shortlists_created_by_user_id
  ON shortlists(created_by_user_id);


-- Intro requests submitted from the public shortlist page. Separate
-- from the logged-in /introductions flow because the client viewing
-- the shortlist is NOT an authenticated ExecFlex user.
CREATE TABLE IF NOT EXISTS shortlist_intro_requests (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  shortlist_id    UUID NOT NULL REFERENCES shortlists(id) ON DELETE CASCADE,
  candidate_id    UUID,
  requester_name  TEXT NOT NULL,
  requester_email TEXT NOT NULL,
  message         TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_shortlist_intro_requests_shortlist_id
  ON shortlist_intro_requests(shortlist_id);
