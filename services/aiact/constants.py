"""Constants and namespacing for the ainm AI Act Check surface.

Assessments persist on the existing durable activity_log table, namespaced so
they never collide with recruiter/marketplace activity: entity_type='client'
(the assessment is about the org itself), activity_type='ai_act_assessment',
metadata.aiact=True. Reads always filter on activity_type + metadata.aiact.
"""

# activity_log discriminators.
AIACT_ENTITY_TYPE = "client"  # permitted by the CHECK constraint
AIACT_ACTIVITY_TYPE = "ai_act_assessment"

# Assessment lifecycle.
ASSESSMENT_STATES = ("draft", "scored")

# Rate limit for the scoring endpoint (process-local sliding windows).
SCORE_IP_LIMIT = 20
SCORE_IP_WINDOW_S = 3600
SCORE_ORG_LIMIT = 40
SCORE_ORG_WINDOW_S = 3600

# Input caps for public forms.
MAX_NAME_LEN = 160
MAX_DESC_LEN = 2000
MAX_ANSWER_LEN = 2000
MAX_ANSWERS = 60

# The disclaimer that must appear in the product and the methodology doc.
DISCLAIMER = (
    "This is an EU AI Act readiness and decision-support assessment, not legal "
    "advice. It is generated from your answers using a documented, rule-based "
    "methodology (with an optional AI-generated narrative, clearly marked). It "
    "does not create a legal opinion or guarantee compliance. For binding "
    "conclusions, consult qualified legal counsel."
)
