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
    Normalize candidate data from executive_profiles table.
    Handles executive_profiles schema fields directly.
    """
    # --- name (executive_profiles has first_name, last_name)
    first = raw.get("first_name") or ""
    last = raw.get("last_name") or ""
    name = " ".join([p for p in [first, last] if p]).strip() or "Unknown Exec"

    # --- role (executive_profiles has headline)
    role = raw.get("headline") or ""

    # --- industries (executive_profiles has industries array)
    industries_val = raw.get("industries") or []
    industries = _to_set(industries_val)

    # --- expertise (executive_profiles has expertise array)
    expertise_val = raw.get("expertise") or raw.get("skills") or []
    expertise = _to_set(expertise_val)

    # --- availability (executive_profiles has availability_type)
    availability = raw.get("availability_type") or ""

    # --- location (executive_profiles has location)
    location = raw.get("location") or "Remote"

    # --- summary (executive_profiles has bio)
    summary = raw.get("bio") or f"{name} — {role}"

    # --- highlights (executive_profiles has achievements array)
    highlights = raw.get("achievements") or []

    # --- experience (executive_profiles has years_of_experience)
    exp = raw.get("years_of_experience") or 0
    exp = _digits_int(exp)

    # --- compensation (executive_profiles has rate_range JSON)
    comp = 0
    rate_range = raw.get("rate_range")
    if rate_range:
        if isinstance(rate_range, dict):
            # Try to extract numeric value from rate_range JSON
            comp = _digits_int(rate_range.get("min")) or _digits_int(rate_range.get("max")) or _digits_int(rate_range.get("amount"))
        else:
            comp = _digits_int(rate_range)

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
        "_raw": raw,
        "email": raw.get("email") or "candidate@example.com",  # fallback
    }


def _score(cand: dict, industry: str, expertise: str, availability: str, location: str, max_salary: int) -> int:
    score = 0
    if industry and industry.lower() in cand["industries"]:
        score += 3
    if expertise:
        req_tokens = {t.strip().lower() for t in re.split(r"[,/;|\s]+", expertise) if t.strip()}
        if req_tokens & cand["expertise"]:
            score += 3
        elif any(t in (cand["role"] or "").lower() for t in req_tokens):
            score += 2
    if availability and availability.lower() in cand["availability"]:
        score += 1
    if location and (location.lower() in cand["location"].lower() or "remote" in cand["location"].lower()):
        score += 1
    if max_salary and cand["comp_expectation"] and cand["comp_expectation"] > max_salary:
        score -= 2
    return score


def _fetch_candidates_from_supabase():
    """Fetch candidates from executive_profiles table. Raises error if Supabase is unavailable."""
    sb = _get_supabase()
    try:
        res = sb.table("executive_profiles").select("*").execute()
        data = res.data or []
        return data
    except Exception as e:
        raise RuntimeError(f"Failed to fetch candidates from Supabase: {e}") from e


def find_best_match(industry: str, expertise: str, availability: str, min_experience: int, max_salary: int, location: str):
    """
    Find best matching candidates from Supabase.
    Raises error if Supabase is unavailable or query fails.
    """
    # 1) load candidates from Supabase
    rows = _fetch_candidates_from_supabase()
    if not rows:
        print("⚠️ No candidates found in Supabase executive_profiles table")
        return []

    # 2) normalize
    cands = [_norm_candidate(r) for r in rows]
    print(f"Pulled {len(cands)} candidates from Supabase:executive_profiles")

    # 3) filter by minimum experience
    filtered = []
    for c in cands:
        if min_experience and c["experience_years"] and c["experience_years"] < int(min_experience):
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

    # 5) return top 5 matches
    return filtered[:5]
