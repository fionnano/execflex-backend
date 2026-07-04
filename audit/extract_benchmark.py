"""
ExecFlex → Pay Benchmarking Extraction Pipeline

THIS SCRIPT IS DISABLED. DO NOT RUN AGAINST PRODUCTION DATA WITHOUT:
1. Legal review of GDPR_QUESTIONS.md (Gates 1-3 must be cleared)
2. DPIA completion
3. Manual review of output for re-identification risk

To enable: remove the sys.exit() call at the bottom of this file.

Usage (after legal clearance):
    export SUPABASE_URL=<your-supabase-url>
    export SUPABASE_SERVICE_KEY=<your-service-key>
    python extract_benchmark.py --output ./output/ --k-threshold 5
"""

import json
import os
import re
import sys
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from typing import Optional


# ─── Configuration ────────────────────────────────────────────────────────────

K_ANONYMITY_THRESHOLD = 5

# Fixed exchange rates (snapshot — update before any real extraction)
FX_RATES_TO_EUR = {
    "EUR": 1.0,
    "GBP": 1.17,
    "USD": 0.92,
    "CHF": 1.04,
}

# ─── Enums ────────────────────────────────────────────────────────────────────

class DataSource(str, Enum):
    PROFILE_STATED = "profile_stated"
    PLACEMENT_ACTUAL = "placement_actual"
    OPPORTUNITY_LISTED = "opportunity_listed"

class RoleCategory(str, Enum):
    CEO = "CEO"
    CFO = "CFO"
    CTO = "CTO"
    COO = "COO"
    CMO = "CMO"
    CHRO = "CHRO"
    CRO = "CRO"
    NED = "NED"
    VP_ENGINEERING = "VP_Engineering"
    VP_SALES = "VP_Sales"
    VP_PRODUCT = "VP_Product"
    DIRECTOR_FINANCE = "Director_Finance"
    DIRECTOR_OPERATIONS = "Director_Operations"
    DIRECTOR_TECHNOLOGY = "Director_Technology"
    SENIOR_MANAGER = "Senior_Manager"
    MANAGER = "Manager"
    CONSULTANT = "Consultant"
    OTHER = "Other"

class SeniorityBand(str, Enum):
    C_SUITE = "c_suite"
    VP = "vp"
    DIRECTOR = "director"
    SENIOR_MANAGER = "senior_manager"
    MANAGER = "manager"
    OTHER = "other"

class ExperienceBand(str, Enum):
    BAND_5_10 = "5_10"
    BAND_10_15 = "10_15"
    BAND_15_20 = "15_20"
    BAND_20_25 = "20_25"
    BAND_25_PLUS = "25_plus"

class LocationRegion(str, Enum):
    IRELAND = "ireland"
    UK = "uk"
    EU = "eu"
    US = "us"
    OTHER = "other"

class EngagementType(str, Enum):
    FULL_TIME = "full_time"
    FRACTIONAL = "fractional"
    CONTRACT = "contract"
    NED = "ned"
    ADVISORY = "advisory"

class CompanySizeBand(str, Enum):
    STARTUP = "startup"
    SME = "sme"
    MID_MARKET = "mid_market"
    ENTERPRISE = "enterprise"
    UNKNOWN = "unknown"

class CompensationType(str, Enum):
    ANNUAL_SALARY = "annual_salary"
    DAILY_RATE = "daily_rate"
    HOURLY_RATE = "hourly_rate"


# ─── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class BenchmarkRecord:
    record_id: str
    data_source: str
    role_category: str
    seniority_band: str
    experience_band: str
    industry: str
    expertise_area: Optional[str]
    location_region: str
    engagement_type: str
    is_remote: bool
    company_size_band: str
    compensation_type: str
    compensation_min_eur: int
    compensation_max_eur: int
    placement_confirmed: bool
    record_quarter: str
    k_group_size: int = 0


# ─── Normalization Functions ──────────────────────────────────────────────────

_ROLE_PATTERNS = [
    (r"\bCEO\b|Chief Executive", RoleCategory.CEO),
    (r"\bCFO\b|Chief Financial", RoleCategory.CFO),
    (r"\bCTO\b|Chief Technolog", RoleCategory.CTO),
    (r"\bCOO\b|Chief Operat", RoleCategory.COO),
    (r"\bCMO\b|Chief Marketing", RoleCategory.CMO),
    (r"\bCHRO\b|Chief Human|Chief People", RoleCategory.CHRO),
    (r"\bCRO\b|Chief Revenue", RoleCategory.CRO),
    (r"\bNED\b|Non.Executive Director|Board Member", RoleCategory.NED),
    (r"VP.*Eng|Vice President.*Eng", RoleCategory.VP_ENGINEERING),
    (r"VP.*Sales|Vice President.*Sales", RoleCategory.VP_SALES),
    (r"VP.*Product|Vice President.*Product", RoleCategory.VP_PRODUCT),
    (r"Director.*Financ", RoleCategory.DIRECTOR_FINANCE),
    (r"Director.*Operat", RoleCategory.DIRECTOR_OPERATIONS),
    (r"Director.*Tech|Director.*Eng", RoleCategory.DIRECTOR_TECHNOLOGY),
    (r"Senior Manager|Head of", RoleCategory.SENIOR_MANAGER),
    (r"\bManager\b", RoleCategory.MANAGER),
    (r"Consultant|Advisor", RoleCategory.CONSULTANT),
]

_SENIORITY_MAP = {
    RoleCategory.CEO: SeniorityBand.C_SUITE,
    RoleCategory.CFO: SeniorityBand.C_SUITE,
    RoleCategory.CTO: SeniorityBand.C_SUITE,
    RoleCategory.COO: SeniorityBand.C_SUITE,
    RoleCategory.CMO: SeniorityBand.C_SUITE,
    RoleCategory.CHRO: SeniorityBand.C_SUITE,
    RoleCategory.CRO: SeniorityBand.C_SUITE,
    RoleCategory.NED: SeniorityBand.C_SUITE,
    RoleCategory.VP_ENGINEERING: SeniorityBand.VP,
    RoleCategory.VP_SALES: SeniorityBand.VP,
    RoleCategory.VP_PRODUCT: SeniorityBand.VP,
    RoleCategory.DIRECTOR_FINANCE: SeniorityBand.DIRECTOR,
    RoleCategory.DIRECTOR_OPERATIONS: SeniorityBand.DIRECTOR,
    RoleCategory.DIRECTOR_TECHNOLOGY: SeniorityBand.DIRECTOR,
    RoleCategory.SENIOR_MANAGER: SeniorityBand.SENIOR_MANAGER,
    RoleCategory.MANAGER: SeniorityBand.MANAGER,
    RoleCategory.CONSULTANT: SeniorityBand.OTHER,
    RoleCategory.OTHER: SeniorityBand.OTHER,
}

_IRELAND_KEYWORDS = ["ireland", "dublin", "cork", "galway", "limerick", "waterford", "co."]
_UK_KEYWORDS = ["uk", "united kingdom", "london", "manchester", "birmingham", "edinburgh", "glasgow", "england", "scotland", "wales"]
_US_KEYWORDS = ["us", "usa", "united states", "new york", "san francisco", "california", "texas", "boston", "chicago"]
_EU_KEYWORDS = ["germany", "france", "netherlands", "spain", "italy", "belgium", "austria", "berlin", "paris", "amsterdam", "eu"]


def classify_role(title: str) -> RoleCategory:
    if not title:
        return RoleCategory.OTHER
    for pattern, category in _ROLE_PATTERNS:
        if re.search(pattern, title, re.IGNORECASE):
            return category
    return RoleCategory.OTHER


def get_seniority(role: RoleCategory) -> SeniorityBand:
    return _SENIORITY_MAP.get(role, SeniorityBand.OTHER)


def band_experience(years: Optional[int]) -> ExperienceBand:
    if years is None:
        return ExperienceBand.BAND_10_15  # default assumption for senior profiles
    if years < 10:
        return ExperienceBand.BAND_5_10
    if years < 15:
        return ExperienceBand.BAND_10_15
    if years < 20:
        return ExperienceBand.BAND_15_20
    if years < 25:
        return ExperienceBand.BAND_20_25
    return ExperienceBand.BAND_25_PLUS


def classify_location(location: Optional[str]) -> LocationRegion:
    if not location:
        return LocationRegion.OTHER
    loc_lower = location.lower()
    if any(kw in loc_lower for kw in _IRELAND_KEYWORDS):
        return LocationRegion.IRELAND
    if any(kw in loc_lower for kw in _UK_KEYWORDS):
        return LocationRegion.UK
    if any(kw in loc_lower for kw in _EU_KEYWORDS):
        return LocationRegion.EU
    if any(kw in loc_lower for kw in _US_KEYWORDS):
        return LocationRegion.US
    return LocationRegion.OTHER


def classify_company_size(size: Optional[str]) -> CompanySizeBand:
    if not size:
        return CompanySizeBand.UNKNOWN
    size_lower = size.lower()
    if any(kw in size_lower for kw in ["1-10", "1-50", "startup", "seed", "pre-"]):
        return CompanySizeBand.STARTUP
    if any(kw in size_lower for kw in ["51-200", "50-250", "sme", "small"]):
        return CompanySizeBand.SME
    if any(kw in size_lower for kw in ["201-1000", "250-2000", "mid"]):
        return CompanySizeBand.MID_MARKET
    if any(kw in size_lower for kw in ["1000+", "2000+", "enterprise", "large", "10000"]):
        return CompanySizeBand.ENTERPRISE
    return CompanySizeBand.UNKNOWN


def classify_engagement(availability_type: Optional[str], is_ned: bool = False) -> EngagementType:
    if is_ned:
        return EngagementType.NED
    if not availability_type:
        return EngagementType.FULL_TIME
    at = availability_type.lower()
    if "fractional" in at or "part" in at:
        return EngagementType.FRACTIONAL
    if "contract" in at:
        return EngagementType.CONTRACT
    if "advisory" in at:
        return EngagementType.ADVISORY
    return EngagementType.FULL_TIME


def round_salary(amount: float) -> int:
    return int(round(amount / 5000) * 5000)


def round_daily_rate(amount: float) -> int:
    return int(round(amount / 50) * 50)


def round_hourly_rate(amount: float) -> int:
    return int(round(amount / 10) * 10)


def to_eur(amount: float, currency: str) -> float:
    rate = FX_RATES_TO_EUR.get(currency.upper(), 1.0)
    return amount * rate


def to_quarter(date_str: Optional[str]) -> str:
    if not date_str:
        return "unknown"
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        q = (dt.month - 1) // 3 + 1
        return f"{dt.year}-Q{q}"
    except (ValueError, TypeError):
        return "unknown"


def detect_compensation_type(rate_range: dict) -> CompensationType:
    """Infer compensation type from rate range values."""
    min_val = rate_range.get("min", 0)
    max_val = rate_range.get("max", 0)
    avg = (min_val + max_val) / 2 if min_val and max_val else min_val or max_val
    if avg > 500:  # likely annual
        return CompensationType.ANNUAL_SALARY
    if avg > 100:  # likely daily
        return CompensationType.DAILY_RATE
    return CompensationType.HOURLY_RATE


# ─── k-Anonymity Enforcement ─────────────────────────────────────────────────

def quasi_identifier_key(record: BenchmarkRecord) -> str:
    return "|".join([
        record.role_category,
        record.seniority_band,
        record.experience_band,
        record.industry or "unknown",
        record.location_region,
        record.engagement_type,
        record.company_size_band,
    ])


def enforce_k_anonymity(records: list[BenchmarkRecord], k: int) -> tuple[list[BenchmarkRecord], dict]:
    groups: dict[str, list[BenchmarkRecord]] = {}
    for r in records:
        key = quasi_identifier_key(r)
        groups.setdefault(key, []).append(r)

    kept = []
    suppressed_count = 0
    for key, group in groups.items():
        if len(group) >= k:
            for r in group:
                r.k_group_size = len(group)
            kept.extend(group)
        else:
            suppressed_count += len(group)

    stats = {
        "total_input": len(records),
        "total_kept": len(kept),
        "total_suppressed": suppressed_count,
        "suppression_rate": round(suppressed_count / max(len(records), 1), 3),
        "unique_groups": len(groups),
        "groups_kept": sum(1 for g in groups.values() if len(g) >= k),
        "groups_suppressed": sum(1 for g in groups.values() if len(g) < k),
        "k_threshold": k,
    }
    return kept, stats


# ─── Extraction (Source Queries) ──────────────────────────────────────────────

def extract_profiles(supabase) -> list[dict]:
    """Extract candidate profiles with compensation data."""
    result = (
        supabase.table("people_profiles")
        .select("id,headline,years_experience,industries,expertise,location,"
                "availability_type,is_ned_available,rate_range,profile_source,created_at")
        .eq("approved", True)
        .not_.is_("rate_range", "null")
        .eq("consent_given", True)
        .execute()
    )
    return result.data or []


def extract_placements(supabase) -> list[dict]:
    """Extract confirmed placements with salary data."""
    result = (
        supabase.table("placements")
        .select("id,role_title,annual_salary,fee_percentage,placed_at,"
                "opportunities(industry,location,is_remote,commitment_type),"
                "organizations(industry,size,location)")
        .in_("status", ["invoiced", "paid"])
        .execute()
    )
    return result.data or []


def extract_opportunities(supabase) -> list[dict]:
    """Extract opportunities with stated compensation."""
    result = (
        supabase.table("opportunities")
        .select("id,title,compensation,industry,location,is_remote,"
                "commitment_type,type,organizations(size),created_at")
        .not_.is_("compensation", "null")
        .eq("status", "open")
        .execute()
    )
    return result.data or []


# ─── Transform ────────────────────────────────────────────────────────────────

def transform_profile(profile: dict) -> Optional[BenchmarkRecord]:
    """Transform a people_profiles record into a benchmark record."""
    rate_range = profile.get("rate_range")
    if not rate_range or not isinstance(rate_range, dict):
        return None

    min_val = rate_range.get("min")
    max_val = rate_range.get("max")
    currency = rate_range.get("currency", "EUR")

    if not min_val and not max_val:
        return None

    min_val = float(min_val or max_val)
    max_val = float(max_val or min_val)

    comp_type = detect_compensation_type(rate_range)
    min_eur = to_eur(min_val, currency)
    max_eur = to_eur(max_val, currency)

    if comp_type == CompensationType.ANNUAL_SALARY:
        min_eur = round_salary(min_eur)
        max_eur = round_salary(max_eur)
    elif comp_type == CompensationType.DAILY_RATE:
        min_eur = round_daily_rate(min_eur)
        max_eur = round_daily_rate(max_eur)
    else:
        min_eur = round_hourly_rate(min_eur)
        max_eur = round_hourly_rate(max_eur)

    role = classify_role(profile.get("headline", ""))
    industries = profile.get("industries") or []
    expertise_list = profile.get("expertise") or []

    return BenchmarkRecord(
        record_id=str(uuid.uuid4()),
        data_source=DataSource.PROFILE_STATED.value,
        role_category=role.value,
        seniority_band=get_seniority(role).value,
        experience_band=band_experience(profile.get("years_experience")).value,
        industry=industries[0] if industries else "Other",
        expertise_area=expertise_list[0] if expertise_list else None,
        location_region=classify_location(profile.get("location")).value,
        engagement_type=classify_engagement(
            profile.get("availability_type"),
            profile.get("is_ned_available", False)
        ).value,
        is_remote=False,  # not tracked at profile level
        company_size_band=CompanySizeBand.UNKNOWN.value,
        compensation_type=comp_type.value,
        compensation_min_eur=int(min_eur),
        compensation_max_eur=int(max_eur),
        placement_confirmed=False,
        record_quarter=to_quarter(profile.get("created_at")),
    )


def transform_placement(placement: dict) -> Optional[BenchmarkRecord]:
    """Transform a placements record into a benchmark record."""
    salary = placement.get("annual_salary")
    if not salary:
        return None

    salary_eur = round_salary(float(salary))  # assumed EUR from Irish market context
    role = classify_role(placement.get("role_title", ""))

    opp = placement.get("opportunities") or {}
    org = placement.get("organizations") or {}

    return BenchmarkRecord(
        record_id=str(uuid.uuid4()),
        data_source=DataSource.PLACEMENT_ACTUAL.value,
        role_category=role.value,
        seniority_band=get_seniority(role).value,
        experience_band=ExperienceBand.BAND_15_20.value,  # not available from placements
        industry=opp.get("industry") or org.get("industry") or "Other",
        expertise_area=None,
        location_region=classify_location(opp.get("location") or org.get("location")).value,
        engagement_type=classify_engagement(opp.get("commitment_type")).value,
        is_remote=bool(opp.get("is_remote")),
        company_size_band=classify_company_size(org.get("size")).value,
        compensation_type=CompensationType.ANNUAL_SALARY.value,
        compensation_min_eur=salary_eur,
        compensation_max_eur=salary_eur,
        placement_confirmed=True,
        record_quarter=to_quarter(placement.get("placed_at")),
    )


# ─── Synthetic Data Tests ─────────────────────────────────────────────────────

def run_synthetic_tests():
    """Validate anonymisation rules using synthetic data. No real data accessed."""

    print("Running synthetic data tests...")

    # Test 1: Role classification
    assert classify_role("Chief Technology Officer") == RoleCategory.CTO
    assert classify_role("CEO & Founder") == RoleCategory.CEO
    assert classify_role("VP Engineering") == RoleCategory.VP_ENGINEERING
    assert classify_role("Non-Executive Director") == RoleCategory.NED
    assert classify_role("Senior Manager, Operations") == RoleCategory.SENIOR_MANAGER
    assert classify_role("Software Developer") == RoleCategory.OTHER
    print("  [PASS] Role classification")

    # Test 2: Experience banding
    assert band_experience(7) == ExperienceBand.BAND_5_10
    assert band_experience(12) == ExperienceBand.BAND_10_15
    assert band_experience(18) == ExperienceBand.BAND_15_20
    assert band_experience(22) == ExperienceBand.BAND_20_25
    assert band_experience(30) == ExperienceBand.BAND_25_PLUS
    assert band_experience(None) == ExperienceBand.BAND_10_15
    print("  [PASS] Experience banding")

    # Test 3: Location classification
    assert classify_location("Dublin, Ireland") == LocationRegion.IRELAND
    assert classify_location("London, UK") == LocationRegion.UK
    assert classify_location("Berlin, Germany") == LocationRegion.EU
    assert classify_location("San Francisco, CA") == LocationRegion.US
    assert classify_location("Tokyo, Japan") == LocationRegion.OTHER
    assert classify_location(None) == LocationRegion.OTHER
    print("  [PASS] Location classification")

    # Test 4: Compensation rounding
    assert round_salary(127000) == 125000
    assert round_salary(128000) == 130000
    assert round_daily_rate(1175) == 1200
    assert round_daily_rate(1124) == 1100
    assert round_hourly_rate(87) == 90
    assert round_hourly_rate(83) == 80
    print("  [PASS] Compensation rounding")

    # Test 5: Currency conversion
    assert to_eur(100000, "EUR") == 100000
    assert to_eur(100000, "GBP") == 117000
    assert to_eur(100000, "USD") == 92000
    print("  [PASS] Currency conversion")

    # Test 6: k-Anonymity enforcement
    synthetic_records = []
    # Create 10 records in one group (should survive k=5)
    for i in range(10):
        synthetic_records.append(BenchmarkRecord(
            record_id=str(uuid.uuid4()),
            data_source="profile_stated",
            role_category="CFO",
            seniority_band="c_suite",
            experience_band="15_20",
            industry="Financial Services",
            expertise_area="Finance",
            location_region="ireland",
            engagement_type="full_time",
            is_remote=False,
            company_size_band="mid_market",
            compensation_type="annual_salary",
            compensation_min_eur=150000,
            compensation_max_eur=180000,
            placement_confirmed=False,
            record_quarter="2025-Q3",
        ))
    # Create 3 records in another group (should be suppressed at k=5)
    for i in range(3):
        synthetic_records.append(BenchmarkRecord(
            record_id=str(uuid.uuid4()),
            data_source="placement_actual",
            role_category="CEO",
            seniority_band="c_suite",
            experience_band="25_plus",
            industry="Technology",
            expertise_area="Strategy",
            location_region="ireland",
            engagement_type="full_time",
            is_remote=False,
            company_size_band="enterprise",
            compensation_type="annual_salary",
            compensation_min_eur=300000,
            compensation_max_eur=300000,
            placement_confirmed=True,
            record_quarter="2025-Q2",
        ))

    kept, stats = enforce_k_anonymity(synthetic_records, k=5)
    assert len(kept) == 10, f"Expected 10 kept, got {len(kept)}"
    assert stats["total_suppressed"] == 3, f"Expected 3 suppressed, got {stats['total_suppressed']}"
    assert stats["groups_kept"] == 1
    assert stats["groups_suppressed"] == 1
    assert all(r.k_group_size == 10 for r in kept)
    print("  [PASS] k-Anonymity enforcement")

    # Test 7: No direct identifiers in output
    record = BenchmarkRecord(
        record_id=str(uuid.uuid4()),
        data_source="profile_stated",
        role_category="CTO",
        seniority_band="c_suite",
        experience_band="15_20",
        industry="Technology",
        expertise_area="Technology",
        location_region="ireland",
        engagement_type="fractional",
        is_remote=True,
        company_size_band="sme",
        compensation_type="daily_rate",
        compensation_min_eur=1200,
        compensation_max_eur=1500,
        placement_confirmed=False,
        record_quarter="2025-Q4",
    )
    record_dict = asdict(record)
    forbidden_fields = ["first_name", "last_name", "email", "phone", "linkedin",
                        "user_id", "headshot", "bio", "name"]
    for field in forbidden_fields:
        assert field not in record_dict, f"Forbidden field '{field}' found in output"
    print("  [PASS] No direct identifiers in output schema")

    # Test 8: Quarter coarsening
    assert to_quarter("2025-03-15T10:30:00Z") == "2025-Q1"
    assert to_quarter("2025-07-01T00:00:00Z") == "2025-Q3"
    assert to_quarter("2025-12-31T23:59:59Z") == "2025-Q4"
    assert to_quarter(None) == "unknown"
    print("  [PASS] Temporal coarsening")

    # Test 9: Profile transform produces valid record
    fake_profile = {
        "headline": "Chief Financial Officer",
        "years_experience": 18,
        "industries": ["Financial Services"],
        "expertise": ["Finance"],
        "location": "Dublin",
        "availability_type": "full_time",
        "is_ned_available": False,
        "rate_range": {"min": 150000, "max": 200000, "currency": "EUR"},
        "created_at": "2025-06-15T10:00:00Z",
    }
    result = transform_profile(fake_profile)
    assert result is not None
    assert result.role_category == "CFO"
    assert result.seniority_band == "c_suite"
    assert result.experience_band == "15_20"
    assert result.location_region == "ireland"
    assert result.compensation_min_eur == 150000
    assert result.compensation_max_eur == 200000
    assert result.placement_confirmed is False
    assert result.record_quarter == "2025-Q2"
    print("  [PASS] Profile transformation")

    print("\nAll 9 synthetic tests passed.")
    return True


# ─── Main Pipeline ────────────────────────────────────────────────────────────

def run_pipeline(output_dir: str, k_threshold: int = K_ANONYMITY_THRESHOLD):
    """Execute the full extraction pipeline. Requires Supabase credentials."""

    from supabase import create_client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")

    if not url or not key:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set.")
        sys.exit(1)

    supabase = create_client(url, key)
    os.makedirs(output_dir, exist_ok=True)

    print(f"Extraction pipeline starting (k={k_threshold})...")

    # Stage 1: Extract
    print("Stage 1: Extracting source data...")
    profiles = extract_profiles(supabase)
    placements = extract_placements(supabase)
    opportunities = extract_opportunities(supabase)
    print(f"  Profiles: {len(profiles)}, Placements: {len(placements)}, Opportunities: {len(opportunities)}")

    # Stage 2: Transform
    print("Stage 2: Normalizing and transforming...")
    records: list[BenchmarkRecord] = []

    for p in profiles:
        r = transform_profile(p)
        if r:
            records.append(r)

    for pl in placements:
        r = transform_placement(pl)
        if r:
            records.append(r)

    print(f"  Transformed: {len(records)} records")

    # Stage 3: Anonymise (k-anonymity)
    print(f"Stage 3: Enforcing k-anonymity (k={k_threshold})...")
    kept, anon_stats = enforce_k_anonymity(records, k_threshold)
    print(f"  Kept: {anon_stats['total_kept']}, Suppressed: {anon_stats['total_suppressed']} "
          f"({anon_stats['suppression_rate']*100:.1f}%)")

    # Stage 4: Output
    print("Stage 4: Writing output...")

    # CSV output
    import csv
    csv_path = os.path.join(output_dir, "benchmark_records.csv")
    if kept:
        fieldnames = list(asdict(kept[0]).keys())
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in kept:
                writer.writerow(asdict(r))
    print(f"  Written: {csv_path} ({len(kept)} records)")

    # Report output
    report = {
        "extraction_timestamp": datetime.utcnow().isoformat() + "Z",
        "k_threshold": k_threshold,
        "source_counts": {
            "profiles_queried": len(profiles),
            "placements_queried": len(placements),
            "opportunities_queried": len(opportunities),
        },
        "transform_counts": {
            "total_transformed": len(records),
            "from_profiles": sum(1 for r in records if r.data_source == "profile_stated"),
            "from_placements": sum(1 for r in records if r.data_source == "placement_actual"),
        },
        "anonymisation_stats": anon_stats,
        "output": {
            "records_in_output": len(kept),
            "csv_path": csv_path,
        },
    }

    report_path = os.path.join(output_dir, "extraction_report.json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"  Written: {report_path}")

    print("\nPipeline complete.")
    return report


# ─── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="ExecFlex pay benchmarking extraction")
    parser.add_argument("--test", action="store_true", help="Run synthetic tests only")
    parser.add_argument("--output", default="./output", help="Output directory")
    parser.add_argument("--k-threshold", type=int, default=K_ANONYMITY_THRESHOLD,
                        help=f"k-anonymity threshold (default: {K_ANONYMITY_THRESHOLD})")
    args = parser.parse_args()

    if args.test:
        success = run_synthetic_tests()
        sys.exit(0 if success else 1)

    # ══════════════════════════════════════════════════════════════════════════
    # DISABLED: This script must not be run against production data without
    # legal review. See GDPR_QUESTIONS.md Gates 1-3.
    #
    # To enable: comment out the following sys.exit() call.
    # ══════════════════════════════════════════════════════════════════════════
    print("ERROR: Pipeline execution is disabled.")
    print("This script must be reviewed and approved before running against real data.")
    print("See audit/GDPR_QUESTIONS.md for the legal gates that must be cleared.")
    print("\nTo run synthetic tests only: python extract_benchmark.py --test")
    sys.exit(1)

    # Uncomment below after legal clearance:
    # run_pipeline(args.output, args.k_threshold)
