"""ainm suite — the unifying shell layer over the estate's products.

This package does NOT change any product's internal workings. It provides a
config-driven module registry and entitlement resolver so a single authenticated
experience can present the modules a user/org is entitled to:

  Internal (one execflex.ai Supabase login — seamless):
    search       → /console        (ainm Search recruiting console)
    marketplace  → /marketplace     (ainm Marketplace)
    aiact        → /ai-act          (EU AI Act Check)
  External (separate apps / separate sign-in — shell-linked, see DECISIONS.md D-20):
    hr           → ainm.ai          (ainm HR platform — LIVE client product)
    transparency → transparency.ainm.ai (pay-equity transparency)

Entitlements are config-driven (env), not a billing integration — enough that
"one suite, modular access" is real and demoable.
"""
