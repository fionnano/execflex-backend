"""ainm Marketplace — a curated two-sided marketplace for pre-vetted AI & data leaders.

A NEW product surface, separate from the /console recruiter product. Persists on
the existing durable, org-scoped tables under a dedicated namespace (see
DECISIONS.md D-14): leaders live in people_profiles under MARKETPLACE_ORG_ID with
source='marketplace_leader', companies+roles in opportunities (metadata.marketplace),
and the billable introductions in activity_log (entity_type='placement').
"""
