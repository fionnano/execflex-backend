"""Marketplace constants and namespacing.

All marketplace rows are namespaced so they never collide with the /console
recruiter product (which filters people_profiles / opportunities by the caller's
own org_id from the JWT — never the marketplace org).
"""

# The single dedicated org that owns all curated marketplace catalog rows
# (leaders, marketplace opportunities). Deterministic UUID so seeding is
# idempotent and the console never queries it.
MARKETPLACE_ORG_ID = "00000000-0000-4000-a000-000000000c0a"

MARKETPLACE_ORG_NAME = "ainm Marketplace"

# people_profiles.source value that marks a curated marketplace leader.
LEADER_SOURCE = "marketplace_leader"

# activity_log.entity_type for the billable introduction event. The column's
# CHECK constraint permits 'placement' — an introduction IS a would-be placement.
INTRO_ENTITY_TYPE = "placement"

# Vetting pass threshold (0-100). At/above → verified + "Independently vetted".
VETTING_PASS_THRESHOLD = 70

# Default placement fee: 15% of first-year total compensation.
DEFAULT_PLACEMENT_FEE_PCT = 15.0

# Introduction lifecycle.
INTRO_STATES = ("requested", "accepted", "declined", "interviewing", "hired", "closed")

# Vetting tracks — a leader is vetted against one track's question set.
VETTING_TRACKS = ("ml_platform", "data_engineering", "ai_product", "applied_research")
