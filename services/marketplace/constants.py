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

# activity_log.activity_type discriminators (all namespaced by metadata.marketplace).
INTRO_ACTIVITY_TYPE = "marketplace_introduction"
COMPANY_ACTIVITY_TYPE = "marketplace_company_profile"

# Vetting pass threshold (0-100). At/above → verified + "Independently vetted".
VETTING_PASS_THRESHOLD = 70

# Default placement fee: 15% of first-year total compensation.
DEFAULT_PLACEMENT_FEE_PCT = 15.0

# Introduction lifecycle.
#   requested    — company asked for an intro (leader not yet notified/decided)
#   accepted     — leader agreed; contact details revealed to the company
#   declined     — leader declined; no contact revealed
#   interviewing — company progressing the accepted leader
#   hired        — placement made → fee realised
#   closed       — company not proceeding (outcome recorded)
INTRO_STATES = ("requested", "accepted", "declined", "interviewing", "hired", "closed")

# States the LEADER controls (respond to a request).
LEADER_RESPONSE_STATES = ("accepted", "declined")

# States the COMPANY controls once a leader has accepted (outcome tracking).
COMPANY_OUTCOME_STATES = ("interviewing", "hired", "closed")

# States in which the leader's private contact details are revealed to the
# requesting company (the leader has opted in).
CONTACT_REVEALED_STATES = ("accepted", "interviewing", "hired")

# Vetting tracks — a leader is vetted against one track's question set.
VETTING_TRACKS = ("ml_platform", "data_engineering", "ai_product", "applied_research")

# Human-readable discipline for each track (used as a search facet).
TRACK_LABELS = {
    "ml_platform": "ML Platform",
    "data_engineering": "Data Engineering",
    "ai_product": "AI Product",
    "applied_research": "Applied Research",
}

# Engagement types a leader offers / a role requires.
ENGAGEMENT_TYPES = ("fractional", "permanent", "both")

# Rate limits for the public vetting-submission endpoint (assessment-farming
# guard). Process-local sliding windows, same pattern as routes/talent_network.py.
VETTING_IP_LIMIT = 8          # submissions per IP …
VETTING_IP_WINDOW_S = 3600    # … per hour
VETTING_LEADER_LIMIT = 3      # attempts per leader profile …
VETTING_LEADER_WINDOW_S = 86400  # … per day

# Input-length caps for public forms (validation.py enforces these).
MAX_NAME_LEN = 120
MAX_HEADLINE_LEN = 200
MAX_BIO_LEN = 4000
MAX_LOCATION_LEN = 120
MAX_MESSAGE_LEN = 2000
MAX_SKILLS = 30
MAX_SKILL_LEN = 60
MAX_SECTORS = 20
MAX_ANSWER_LEN = 6000
