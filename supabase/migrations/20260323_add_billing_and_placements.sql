-- Add billing fields to organizations
ALTER TABLE organizations
  ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT,
  ADD COLUMN IF NOT EXISTS stripe_subscription_id TEXT,
  ADD COLUMN IF NOT EXISTS subscription_status TEXT NOT NULL DEFAULT 'free'
    CHECK (subscription_status IN ('free', 'trialing', 'active', 'canceled')),
  ADD COLUMN IF NOT EXISTS subscription_tier TEXT NOT NULL DEFAULT 'free'
    CHECK (subscription_tier IN ('free', 'growth', 'enterprise')),
  ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMPTZ;

-- Index for Stripe customer lookups
CREATE INDEX IF NOT EXISTS idx_organizations_stripe_customer_id
  ON organizations (stripe_customer_id) WHERE stripe_customer_id IS NOT NULL;

-- Placements table
CREATE TABLE IF NOT EXISTS placements (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id UUID REFERENCES organizations(id),
  opportunity_id UUID REFERENCES opportunities(id),
  candidate_user_id UUID,
  role_title TEXT NOT NULL,
  annual_salary NUMERIC(12, 2),
  fee_percentage NUMERIC(5, 2),
  fee_amount NUMERIC(12, 2),
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'invoiced', 'paid')),
  placed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_placements_organization_id ON placements (organization_id);
CREATE INDEX IF NOT EXISTS idx_placements_opportunity_id ON placements (opportunity_id);
