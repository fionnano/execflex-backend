-- SEC-E-003 (audit 2026-10-16): screening_bias_audit had no tenant column, so
-- GET /screening/bias-audit/<role_id> returned every organisation's audit rows.
--
-- Adds organization_id, indexes it with role_id (the query the route runs), and
-- backfills existing rows from the opportunity the row references (role_id holds
-- the opportunity UUID for console-started screenings) or, failing that, from
-- the organisation of the user who created the call job.
--
-- Application code (services/screening_service.py) stamps organization_id on
-- every new row and routes/screening.py filters every read by it. Rows that
-- cannot be attributed to an organisation stay NULL and are not served.
--
-- Apply BEFORE deploying the matching code. Safe to run more than once.

ALTER TABLE screening_bias_audit
  ADD COLUMN IF NOT EXISTS organization_id UUID;

CREATE INDEX IF NOT EXISTS idx_screening_bias_audit_org_role
  ON screening_bias_audit (organization_id, role_id);

-- Backfill 1: via the opportunity referenced by role_id (TEXT column, so compare
-- as text rather than casting, which would fail on non-UUID legacy values).
UPDATE screening_bias_audit b
SET organization_id = o.organization_id
FROM opportunities o
WHERE b.organization_id IS NULL
  AND b.role_id IS NOT NULL
  AND o.id::text = lower(b.role_id)
  AND o.organization_id IS NOT NULL;

-- Backfill 2: via the organisation of the user who created the call job.
UPDATE screening_bias_audit b
SET organization_id = ur.organization_id
FROM outbound_call_jobs j
JOIN user_roles ur ON ur.user_id::text = j.user_id::text
WHERE b.organization_id IS NULL
  AND j.id = b.job_id;
