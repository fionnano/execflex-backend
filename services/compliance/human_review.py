"""
Human review gate — EU AI Act compliance.

Terminal candidate decisions (reject, withdrawn) MUST have human review
before execution. This prevents fully automated rejection, which is
prohibited under GDPR Art. 22 and EU AI Act for high-risk AI systems.
"""
from typing import Dict


def require_human_review_for_reject(ctx, entity_id: str, reason: str) -> Dict:
    """Check that a reject/withdrawal has human authorization.

    Returns {"allowed": True/False, "message": str}.
    The ctx (OrgContext) proves a human is making the decision — the
    requirement is that no automated process can reject without human sign-off.
    """
    if not ctx or not ctx.user_id:
        return {
            "allowed": False,
            "message": "Terminal decisions (reject/withdraw) require human authorization. "
                       "Automated rejection is not permitted under EU AI Act for high-risk AI systems."
        }

    if not reason or len(reason.strip()) < 3:
        return {
            "allowed": False,
            "message": "A reason is required for terminal decisions (reject/withdraw). "
                       "This supports the candidate's right to explanation under GDPR Art. 22."
        }

    return {"allowed": True, "message": "Human review confirmed"}
