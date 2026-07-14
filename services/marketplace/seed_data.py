"""Synthetic seed pool for the ainm Marketplace — zero real people.

15 vetted AI/data leaders across four tracks, 8 companies, 6 open opportunities,
and a handful of introductions in varied states. All ids are deterministic
(uuid5 over a fixed namespace) so seeding is idempotent.
"""
from __future__ import annotations

import uuid

_NS = uuid.UUID("11111111-2222-4333-8444-555555555555")


def _id(kind: str, key: str) -> str:
    return str(uuid.uuid5(_NS, f"{kind}:{key}"))


# ── Companies (demand side) ──────────────────────────────────────────────────
COMPANIES = [
    {"id": _id("company", "nviro"), "name": "Nviro Analytics", "sector": "Climate Tech",
     "size": "50-200", "location": "Dublin, IE", "website": "nviro.example"},
    {"id": _id("company", "medalytic"), "name": "Medalytic", "sector": "HealthTech",
     "size": "200-500", "location": "London, UK", "website": "medalytic.example"},
    {"id": _id("company", "finstack"), "name": "FinStack", "sector": "FinTech",
     "size": "50-200", "location": "Berlin, DE", "website": "finstack.example"},
    {"id": _id("company", "gridsense"), "name": "GridSense", "sector": "Energy",
     "size": "200-500", "location": "Amsterdam, NL", "website": "gridsense.example"},
    {"id": _id("company", "retabl"), "name": "Retabl", "sector": "Retail / E-commerce",
     "size": "500-1000", "location": "Paris, FR", "website": "retabl.example"},
    {"id": _id("company", "aurora"), "name": "Aurora Mobility", "sector": "Autonomous / Mobility",
     "size": "200-500", "location": "Munich, DE", "website": "aurora.example"},
    {"id": _id("company", "lexair"), "name": "Lexair", "sector": "Legal Tech",
     "size": "50-200", "location": "Dublin, IE", "website": "lexair.example"},
    {"id": _id("company", "biobyte"), "name": "BioByte", "sector": "BioTech",
     "size": "50-200", "location": "Cambridge, UK", "website": "biobyte.example"},
]
_C = {c["name"]: c for c in COMPANIES}


# ── Leaders (supply side, pre-vetted) ────────────────────────────────────────
# vetting_score drives the badge; all seeded leaders are verified (>=70) except
# two intentionally left 'pending' to demo the supply-side pipeline.
LEADERS = [
    # ML platform
    {"key": "amara-okafor", "name": "Amara Okafor", "track": "ml_platform",
     "headline": "VP ML Platform · ex-hyperscaler, 0→1 feature stores",
     "seniority": "VP / Head of", "engagement": "permanent", "years": 15,
     "comp": "€180k–220k", "location": "Dublin, IE", "score": 92,
     "skills": ["ML Platform", "Feature Stores", "Kubernetes", "Model Serving", "MLOps"],
     "sectors": ["FinTech", "Climate Tech"]},
    {"key": "dmitri-volkov", "name": "Dmitri Volkov", "track": "ml_platform",
     "headline": "Fractional Head of ML Infrastructure",
     "seniority": "Head of", "engagement": "fractional", "years": 12,
     "comp": "€1,100/day", "location": "Berlin, DE", "score": 84,
     "skills": ["MLOps", "Ray", "GPU Scheduling", "Model Serving", "Observability"],
     "sectors": ["Autonomous / Mobility", "Energy"]},
    {"key": "sofia-reyes", "name": "Sofia Reyes", "track": "ml_platform",
     "headline": "Principal ML Systems Engineer → EM",
     "seniority": "Principal", "engagement": "both", "years": 11,
     "comp": "€150k–175k", "location": "Remote (EU)", "score": 78,
     "skills": ["Model Serving", "Latency Optimization", "PyTorch", "Triton", "MLOps"],
     "sectors": ["HealthTech", "Retail / E-commerce"]},
    {"key": "kwame-mensah", "name": "Kwame Mensah", "track": "ml_platform",
     "headline": "Director, ML Platform & Developer Experience",
     "seniority": "Director", "engagement": "permanent", "years": 14,
     "comp": "€170k–200k", "location": "Amsterdam, NL", "score": 88,
     "skills": ["Platform Strategy", "CI/CD for ML", "Feature Stores", "Cost Optimization"],
     "sectors": ["Energy", "FinTech"]},

    # Data engineering
    {"key": "lena-fischer", "name": "Lena Fischer", "track": "data_engineering",
     "headline": "Head of Data Engineering · lakehouse at scale",
     "seniority": "Head of", "engagement": "permanent", "years": 13,
     "comp": "€160k–190k", "location": "Munich, DE", "score": 90,
     "skills": ["Spark", "Lakehouse", "dbt", "Streaming", "Data Governance"],
     "sectors": ["Autonomous / Mobility", "Retail / E-commerce"]},
    {"key": "arjun-nair", "name": "Arjun Nair", "track": "data_engineering",
     "headline": "Fractional Data Platform Lead (GDPR-heavy estates)",
     "seniority": "Lead", "engagement": "fractional", "years": 12,
     "comp": "€950/day", "location": "Dublin, IE", "score": 81,
     "skills": ["Airflow", "Data Lineage", "PII / GDPR", "Snowflake", "Data Quality"],
     "sectors": ["HealthTech", "Legal Tech"]},
    {"key": "yuki-tanaka", "name": "Yuki Tanaka", "track": "data_engineering",
     "headline": "Principal Data Engineer · streaming & CDC",
     "seniority": "Principal", "engagement": "both", "years": 10,
     "comp": "€140k–165k", "location": "Remote (EU)", "score": 76,
     "skills": ["Kafka", "Flink", "CDC", "Iceberg", "Data Modeling"],
     "sectors": ["FinTech", "Energy"]},
    {"key": "grace-obrien", "name": "Grace O'Brien", "track": "data_engineering",
     "headline": "Director of Data · governance & platform",
     "seniority": "Director", "engagement": "permanent", "years": 16,
     "comp": "€175k–205k", "location": "Cork, IE", "score": 86,
     "skills": ["Data Governance", "Databricks", "Team Building", "Data Mesh"],
     "sectors": ["BioTech", "HealthTech"]},

    # AI product
    {"key": "noah-berg", "name": "Noah Berg", "track": "ai_product",
     "headline": "Head of AI Product · LLM features 0→1",
     "seniority": "Head of", "engagement": "permanent", "years": 11,
     "comp": "€165k–195k", "location": "London, UK", "score": 89,
     "skills": ["AI Product", "LLM Eval", "Roadmapping", "RAG", "Responsible AI"],
     "sectors": ["Legal Tech", "FinTech"]},
    {"key": "priya-desai", "name": "Priya Desai", "track": "ai_product",
     "headline": "Fractional CPO for AI-native startups",
     "seniority": "CPO", "engagement": "fractional", "years": 15,
     "comp": "€1,300/day", "location": "Remote (EU)", "score": 91,
     "skills": ["AI Strategy", "0→1 Product", "Evaluation", "GTM", "Discovery"],
     "sectors": ["HealthTech", "Retail / E-commerce"]},
    {"key": "tomas-novak", "name": "Tomas Novak", "track": "ai_product",
     "headline": "Group PM, Applied AI",
     "seniority": "Group PM", "engagement": "both", "years": 9,
     "comp": "€130k–155k", "location": "Prague, CZ", "score": 74,
     "skills": ["Applied AI", "Experimentation", "Analytics", "LLM Eval"],
     "sectors": ["Retail / E-commerce", "Climate Tech"]},

    # Applied research
    {"key": "hannah-cole", "name": "Hannah Cole", "track": "applied_research",
     "headline": "Head of Applied Research · vision + multimodal",
     "seniority": "Head of", "engagement": "permanent", "years": 13,
     "comp": "€175k–210k", "location": "Cambridge, UK", "score": 93,
     "skills": ["Computer Vision", "Multimodal", "Research→Prod", "PyTorch", "Experiment Design"],
     "sectors": ["BioTech", "Autonomous / Mobility"]},
    {"key": "mateo-silva", "name": "Mateo Silva", "track": "applied_research",
     "headline": "Staff Research Scientist · recommender systems",
     "seniority": "Staff", "engagement": "both", "years": 12,
     "comp": "€160k–185k", "location": "Lisbon, PT", "score": 82,
     "skills": ["RecSys", "Causal Inference", "A/B Testing", "Ranking", "Python"],
     "sectors": ["Retail / E-commerce", "FinTech"]},

    # Two pending (supply-side pipeline demo)
    {"key": "iris-lindqvist", "name": "Iris Lindqvist", "track": "ai_product",
     "headline": "Senior AI PM (awaiting vetting)",
     "seniority": "Senior PM", "engagement": "permanent", "years": 8,
     "comp": "€120k–140k", "location": "Stockholm, SE", "score": None,
     "skills": ["AI Product", "Discovery", "Analytics"],
     "sectors": ["FinTech"], "status": "pending"},
    {"key": "omar-haddad", "name": "Omar Haddad", "track": "ml_platform",
     "headline": "ML Platform Engineer (awaiting vetting)",
     "seniority": "Senior", "engagement": "fractional", "years": 9,
     "comp": "€800/day", "location": "Remote (EU)", "score": None,
     "skills": ["MLOps", "Terraform", "Model Serving"],
     "sectors": ["Energy"], "status": "pending"},
]

for _ld in LEADERS:
    _ld["id"] = _id("leader", _ld["key"])


# ── Opportunities (6 open roles) ─────────────────────────────────────────────
OPPORTUNITIES = [
    {"key": "nviro-mlp", "title": "VP, ML Platform", "company": _C["Nviro Analytics"],
     "track": "ml_platform", "sector": "Climate Tech", "commitment": "permanent",
     "location": "Dublin, IE", "remote": True, "min": 180000, "max": 220000,
     "desc": "Own the ML platform powering carbon-forecasting models. Feature "
             "store, serving, and MLOps from the ground up."},
    {"key": "medalytic-de", "title": "Head of Data Engineering", "company": _C["Medalytic"],
     "track": "data_engineering", "sector": "HealthTech", "commitment": "permanent",
     "location": "London, UK", "remote": True, "min": 160000, "max": 190000,
     "desc": "Lead the data platform for clinical analytics under strict GDPR / "
             "medical-data governance."},
    {"key": "finstack-aip", "title": "Head of AI Product", "company": _C["FinStack"],
     "track": "ai_product", "sector": "FinTech", "commitment": "permanent",
     "location": "Berlin, DE", "remote": True, "min": 165000, "max": 195000,
     "desc": "Define and ship LLM-powered features across the FinStack product "
             "with rigorous evaluation and responsible-AI guardrails."},
    {"key": "aurora-research", "title": "Head of Applied Research", "company": _C["Aurora Mobility"],
     "track": "applied_research", "sector": "Autonomous / Mobility", "commitment": "permanent",
     "location": "Munich, DE", "remote": False, "min": 185000, "max": 215000,
     "desc": "Lead multimodal perception research and drive it into the "
             "autonomy stack."},
    {"key": "gridsense-mlp", "title": "Fractional Head of ML Infrastructure",
     "company": _C["GridSense"], "track": "ml_platform", "sector": "Energy",
     "commitment": "fractional", "location": "Amsterdam, NL", "remote": True,
     "min": None, "max": None,
     "desc": "2–3 days/week to stand up ML infra for grid-load forecasting."},
    {"key": "retabl-de", "title": "Principal Data Engineer (Streaming)",
     "company": _C["Retabl"], "track": "data_engineering", "sector": "Retail / E-commerce",
     "commitment": "permanent", "location": "Paris, FR", "remote": True,
     "min": 140000, "max": 170000,
     "desc": "Own real-time inventory and personalisation data streams at scale."},
]
for _op in OPPORTUNITIES:
    _op["id"] = _id("opp", _op["key"])


# ── Introductions (varied states) ────────────────────────────────────────────
# Owned by the marketplace org in the seed so the demo pipeline is populated for
# any viewer. first_year_comp drives the placement-fee display.
INTRODUCTIONS = [
    {"key": "intro-1", "leader": "amara-okafor", "company": "Nviro Analytics",
     "opp": "nviro-mlp", "status": "hired", "first_year_comp": 205000, "fee_pct": 15,
     "message": "Strong platform builder — moving to offer."},
    {"key": "intro-2", "leader": "lena-fischer", "company": "Aurora Mobility",
     "opp": "aurora-research", "status": "interviewing", "first_year_comp": 195000, "fee_pct": 15,
     "message": "Second-round interview scheduled."},
    {"key": "intro-3", "leader": "noah-berg", "company": "FinStack",
     "opp": "finstack-aip", "status": "accepted", "first_year_comp": 185000, "fee_pct": 15,
     "message": "Leader accepted the introduction."},
    {"key": "intro-4", "leader": "priya-desai", "company": "Medalytic",
     "opp": "medalytic-de", "status": "requested", "first_year_comp": None, "fee_pct": 15,
     "message": "Fractional CPO — exploratory intro."},
    {"key": "intro-5", "leader": "hannah-cole", "company": "Aurora Mobility",
     "opp": "aurora-research", "status": "declined", "first_year_comp": None, "fee_pct": 15,
     "message": "Leader not available this quarter."},
]
for _in in INTRODUCTIONS:
    _in["id"] = _id("intro", _in["key"])
