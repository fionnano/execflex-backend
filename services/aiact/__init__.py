"""ainm AI Act Check — an EU AI Act readiness assessment product surface.

A NEW org-scoped product surface (see DECISIONS.md D-18). Lets a company assess
its own AI use (with a hiring/HR emphasis) for EU AI Act risk: risk tier,
obligations they're subject to, gaps, and a readiness score with an explainable
rationale.

The assessment engine is agentic-core's shared compliance module (ported from
governance-platform): deterministic prohibited-practice + snapshot scoring +
obligation mapping, with an optional AI-generated narrative (Sonnet) that is
always marked as AI-generated. Persists per org on existing durable tables under
a namespace (activity_log, activity_type='ai_act_assessment'); no new prod DDL.

This is a readiness / decision-support tool, NOT legal advice.
"""
