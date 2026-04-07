-- Track where a people_profiles row came from (direct, apollo, linkedin, etc.)
ALTER TABLE people_profiles
  ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'direct';

ALTER TABLE people_profiles
  ADD COLUMN IF NOT EXISTS source_metadata JSONB DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_people_profiles_source
  ON people_profiles (source);

-- GIN index for efficient JSONB lookups (e.g. apollo_id, opportunity_id)
CREATE INDEX IF NOT EXISTS idx_people_profiles_source_metadata
  ON people_profiles USING gin (source_metadata);
