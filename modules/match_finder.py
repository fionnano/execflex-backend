# modules/match_finder.py
import os
import re

try:
    from supabase import create_client  # type: ignore
except Exception:
    create_client = None


# ---------- helpers ----------
def _get_supabase():
    """Get Supabase client. Raises error if Supabase is not configured."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_SERVICE_KEY")
    
    if not url or not key:
        raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
    
    if not create_client:
        raise ImportError("Supabase client could not be imported. Install: pip install supabase")
    
    try:
        return create_client(url, key)
    except Exception as e:
        raise RuntimeError(f"Failed to initialize Supabase client: {e}") from e


def _digits_int(x):
    if x is None:
        return 0
    if isinstance(x, (int, float)):
        return int(x)
    nums = [int("".join(re.findall(r"\d+", part))) for part in re.findall(r"\d[\d,\.]*", str(x))]
    return max(nums) if nums else 0


def _to_set(v):
    if v is None:
        return set()
    if isinstance(v, list):
        return {str(x).strip().lower() for x in v if x is not None}
    return {str(v).strip().lower()}


def _norm_candidate(raw: dict) -> dict:
    """
    Normalize candidate data from people_profiles table.
    Handles people_profiles schema fields directly.
    """
    # --- name (people_profiles has first_name, last_name)
    first = raw.get("first_name") or ""
    last = raw.get("last_name") or ""
    name = " ".join([p for p in [first, last] if p]).strip() or "Unknown Exec"

    # --- role (people_profiles has headline)
    role = raw.get("headline") or ""

    # --- industries (people_profiles has industries array - can be enum array or text array)
    industries_val = raw.get("industries") or []
    # Handle both enum arrays and text arrays
    if isinstance(industries_val, list):
        industries = _to_set(industries_val)
    else:
        industries = set()

    # --- expertise (people_profiles has expertise array - can be enum array or text array)
    expertise_val = raw.get("expertise") or raw.get("skills") or []
    # Handle both enum arrays and text arrays
    if isinstance(expertise_val, list):
        expertise = _to_set(expertise_val)
    else:
        expertise = set()

    # --- availability (people_profiles has availability_type)
    availability = raw.get("availability_type") or ""

    # --- location (people_profiles has location)
    location = raw.get("location") or "Remote"

    # --- summary (people_profiles has bio)
    summary = raw.get("bio") or f"{name} — {role}"

    # --- highlights (people_profiles doesn't have achievements, use empty list)
    highlights = []

    # --- experience (people_profiles has years_experience)
    exp = raw.get("years_experience") or 0
    exp = _digits_int(exp)

    # --- compensation (people_profiles has rate_range JSONB)
    comp = 0
    rate_range = raw.get("rate_range")
    if rate_range:
        if isinstance(rate_range, dict):
            # Try to extract numeric value from rate_range JSONB
            comp = _digits_int(rate_range.get("min")) or _digits_int(rate_range.get("max")) or _digits_int(rate_range.get("amount"))
        else:
            comp = _digits_int(rate_range)

    # --- NED availability (people_profiles has is_ned_available)
    is_ned_available = raw.get("is_ned_available") or False

    return {
        "id": raw.get("id") or name.lower().replace(" ", "-"),
        "name": name,
        "role": role,
        "industries": industries,
        "expertise": expertise,
        "availability": str(availability).lower(),
        "location": str(location),
        "experience_years": exp,
        "comp_expectation": comp,
        "summary": summary,
        "highlights": highlights if isinstance(highlights, list) else [str(highlights)],
        "is_ned_available": is_ned_available,
        "_raw": raw,
        "email": raw.get("email") or "candidate@example.com",  # fallback
    }


def _score(cand: dict, industry: str, expertise: str, availability: str, location: str, max_salary: int) -> int:
    score = 0
    
    # Industry matching (only if industry filter is provided)
    if industry and industry.strip():
        industry_lower = industry.lower().strip()
        # Check if any industry matches (handles comma-separated values)
        industry_tokens = {t.strip().lower() for t in re.split(r"[,/;|\s]+", industry) if t.strip()}
        if industry_tokens & cand["industries"]:
            score += 3
    
    # Expertise matching (only if expertise filter is provided)
    if expertise and expertise.strip():
        req_tokens = {t.strip().lower() for t in re.split(r"[,/;|\s]+", expertise) if t.strip()}
        if req_tokens & cand["expertise"]:
            score += 3
        elif any(t in (cand["role"] or "").lower() for t in req_tokens):
            score += 2
    
    # Availability matching (only if availability filter is provided)
    if availability and availability.strip():
        availability_lower = availability.lower().strip()
        # Handle comma-separated availability types
        availability_tokens = {t.strip().lower() for t in re.split(r"[,/;|\s]+", availability) if t.strip()}
        if availability_lower in cand["availability"] or availability_tokens & {cand["availability"]}:
            score += 1
    
    # Location matching (only if location filter is provided)
    if location and location.strip():
        location_lower = location.lower().strip()
        cand_location = (cand["location"] or "").lower()
        if location_lower in cand_location or cand_location in location_lower or "remote" in cand_location:
            score += 1
    
    # Salary filter (only if max_salary is set and meaningful)
    if max_salary and max_salary < 999999 and cand["comp_expectation"] and cand["comp_expectation"] > max_salary:
        score -= 2
    
    return score


def _fetch_candidates_from_supabase():
    """Fetch candidates from people_profiles table. Raises error if Supabase is unavailable."""
    sb = _get_supabase()
    try:
        res = sb.table("people_profiles").select("*").execute()
        data = res.data or []
        return data
    except Exception as e:
        raise RuntimeError(f"Failed to fetch candidates from Supabase: {e}") from e


def find_best_match(industry: str, expertise: str, availability: str, min_experience: int, max_salary: int, location: str, is_ned_only: bool = False):
    """
    Find best matching candidates from Supabase.
    Raises error if Supabase is unavailable or query fails.
    
    Args:
        is_ned_only: If True, only return candidates with is_ned_available = True
    """
    # 1) load candidates from Supabase
    rows = _fetch_candidates_from_supabase()
    if not rows:
        print("⚠️ No candidates found in Supabase people_profiles table")
        return []

    # 2) normalize
    cands = []
    for r in rows:
        try:
            cands.append(_norm_candidate(r))
        except Exception as e:
            print(f"⚠️ Failed to normalize candidate record: {e}")
            print(f"   Record: {r.get('id', 'unknown') if isinstance(r, dict) else 'non-dict'}")
            continue
    print(f"Pulled {len(cands)} candidates from Supabase:people_profiles (from {len(rows)} total records)")

    # 3) filter by NED availability if requested
    if is_ned_only:
        ned_cands = [c for c in cands if c.get("is_ned_available", False)]
        print(f"🔍 Filtering for NED/iNED only: {len(ned_cands)} candidates have is_ned_available = True")
        cands = ned_cands

    # 4) filter by minimum experience (only if min_experience is specified and > 0)
    filtered = []
    for c in cands:
        if min_experience and min_experience > 0 and c["experience_years"] and c["experience_years"] < int(min_experience):
            continue
        filtered.append(c)

    # 4) score + sort
    for c in filtered:
        c["_score"] = _score(c, industry, expertise, availability, location, int(max_salary) if max_salary else 0)
    filtered.sort(key=lambda x: x.get("_score", 0), reverse=True)

    # If no filtered results, return top scored from all candidates
    if not filtered:
        print("⚠️ No candidates matched experience filter, using all candidates")
        for c in cands:
            c["_score"] = _score(c, industry, expertise, availability, location, int(max_salary) if max_salary else 0)
        cands.sort(key=lambda x: x.get("_score", 0), reverse=True)
        filtered = cands[:5]

    print(f"🎯 Returning {len(filtered)} matches")

    # 5) Convert sets to lists for JSON serialization and clean up response
    for match in filtered:
        # Convert industries and expertise sets to lists
        if isinstance(match.get("industries"), set):
            match["industries"] = sorted(list(match["industries"]))
        if isinstance(match.get("expertise"), set):
            match["expertise"] = sorted(list(match["expertise"]))
        # Remove internal fields that shouldn't be in the response
        match.pop("_raw", None)
        # Rename _score to score for public API
        if "_score" in match:
            match["score"] = match.pop("_score")
    
    # 6) return top matches (increased limit for better results)
    # If no filters applied, return more results; otherwise return top matches
    has_filters = bool(industry or expertise or availability or location or (min_experience and min_experience > 0) or (max_salary and max_salary < 999999))
    limit = 100 if not has_filters else 20  # Return more if no filters, fewer if filtered
    return filtered[:limit]
