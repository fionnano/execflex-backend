-- Retainer payments: one-off Stripe PaymentIntents for retained searches.
-- Triggered by POST /billing/create-retainer-payment per opportunity.
CREATE TABLE IF NOT EXISTS retainer_payments (
    id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    opportunity_id             UUID NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
    user_id                    UUID NOT NULL,
    stripe_payment_intent_id   TEXT UNIQUE,
    amount                     NUMERIC(12,2) NOT NULL,
    currency                   TEXT NOT NULL DEFAULT 'eur',
    status                     TEXT NOT NULL DEFAULT 'pending',  -- pending | paid | failed | refunded
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    paid_at                    TIMESTAMPTZ,
    metadata                   JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_retainer_payments_opportunity_id ON retainer_payments (opportunity_id);
CREATE INDEX IF NOT EXISTS idx_retainer_payments_user_id        ON retainer_payments (user_id);
CREATE INDEX IF NOT EXISTS idx_retainer_payments_status         ON retainer_payments (status);
CREATE INDEX IF NOT EXISTS idx_retainer_payments_stripe_pi_id   ON retainer_payments (stripe_payment_intent_id);

-- opportunities.status — ensure 'retained' is usable. If status is a
-- plain TEXT column this is a no-op; if it's an enum, run the
-- appropriate ALTER TYPE ... ADD VALUE in the dashboard first.
