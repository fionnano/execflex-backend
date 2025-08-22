# modules/match_finder.py
import os, re, json, pathlib
from datetime import datetime

try:
    from supabase import create_client  # type: ignore
except Exception:
    create_client = None

# ---------- helpers ----------
def _get_supabase():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")
    if not (url and key and create_client):
        return None
    try:
        return create_client(url, key)
    except Exception as e:
        print("⚠️ Supabase init failed in match_finder:", e)
        return None

def _digits_int(x):
    """Pull digits from strings like '€8k-€12k/m' -> 812, etc. Returns int or 0."""
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
    """Normalize a candidate from many possible schemas."""
    name = raw.get("name") or raw.get("full_name") or raw.get("candidate_name") or "Unknown Exec"
    role = raw.get("role") or raw.get("title") or raw.get("headline") or ""
    industries = (
        raw.get("industry") if isinstance(raw.get("industry"), list) else
        raw.get("industries") if raw.get("industries") is not None else
        raw.get("sectors") or []
    )
    expertise = raw.get("expertise") or raw.get("skills") or raw.get("tags") or []
    availability = raw.get("availability") or raw.get("commitment") or ""
    location = raw.get("location") or raw.get("city") or raw.get("region") or "Remote"
    summary = raw.get("summary") or raw.get("bio") or raw.get("about") or f"{name} — {role}"
    highlights = raw.get("highlights") or raw.get("achievements") or []

    exp = (
        raw.get("experience_years")
        or raw.get("years_experience")
        or raw.get("exp_years")
        or raw.get("experience")
        or 0
    )
    exp = _digits_int(exp)

    comp = (
        raw.get("salary_expectation")
        or raw.get("day_rate")
        or raw.get("daily_rate_usd")
        or raw.get("rate")
        or raw.get("compensation")
        or raw.get("budget")
        or 0
    )
    comp = _digits_int(comp)

    return {
        "id": raw.get("id") or raw.get("uuid") or raw.get("candidate_id") or name.lower().replace(" ", "-"),
        "name": name,
        "role": role,
        "industries": _to_set(industries),
        "expertise": _to_set(expertise),
        "availability": str(availability).lower(),
        "location": str(location),
        "experience_years": exp,
        "comp_expectation": comp,  # generic numeric (soft cap)
        "summary": summary,
        "highlights": highlights if isinstance(highlights, list) else [str(highlights)],
        "_raw": raw,
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
        score -= 2  # soft penalty so we still return a suggestion
    return score

def _load_local_json():
    path = pathlib.Path(__file__).resolve().parents[1] / "matches.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            print("⚠️ Failed to read local matches.json:", e)
    return [{
        "id": "cand-001",
        "name": "Alex Byrne",
        "role": "Fractional CRO",
        "industry": ["saas", "fintech"],
        "culture": ["hands-on", "data-led"],
        "summary": "18+ years scaling B2B SaaS revenue from Series A to C.",
        "highlights": ["Built SDR->AE engine", "RevOps discipline", "EMEA expansion"],
        "location": "Dublin, IE",
        "experience_years": 12,
        "day_rate": "1200",
        "availability": "2-3 days/week"
    }]

def _fetch_candidates_from_supabase():
    sb = _get_supabase()
    if not sb:
        return [], None
    tables = ["executive_profiles", "candidates", "matches", "profiles"]
    for t in tables:
        try:
            res = sb.table(t).select("*").execute()
            data = res.data or []
            if data:
                return data, t
        except Exception:
            continue
    return [], None

def _log_search_event(params: dict, results_count: int, fallback_used: bool):
    sb = _get_supabase()
    if not sb:
        return
    try:
        sb.table("search_events").insert({
            "created_at": datetime.utcnow().isoformat() + "Z",
            **params,
            "results_count": results_count,
            "fallback_used": fallback_used,
        }).execute()
    except Exception as e:
        print("ℹ️ search_events insert skipped:", e)

def find_best_match(industry: str, expertise: str, availability: str, min_experience: int, max_salary: int, location: str):
    # 1) load candidates (Supabase first, then local fallback)
    rows, table_used = _fetch_candidates_from_supabase()
    source = f"Supabase:{table_used}" if table_used else "local:matches.json"
    if not rows:
        rows = _load_local_json()

    # 2) normalize
    cands = [_norm_candidate(r) for r in rows]
    print(f"Pulled {len(cands)} candidates from {source}")

    # 3) hard filter (experience only)
    filtered = []
    for c in cands:
        if min_experience and c["experience_years"] and c["experience_years"] < int(min_experience):
            continue
        filtered.append(c)

    # 4) score and sort
    for c in filtered:
        c["_score"] = _score(c, industry, expertise, availability, location, int(max_salary) if max_salary else 0)
    filtered.sort(key=lambda x: x.get("_score", 0), reverse=True)

    fallback_used = False
    if not filtered:
        # if everything filtered out, loosen constraints and still return a suggestion
        fallback_used = True
        for c in cands:
            c["_score"] = _score(c, industry, expertise, availability, location, int(max_salary) if max_salary else 0)
        cands.sort(key=lambda x: x.get("_score", 0), reverse=True)
        filtered = cands[:5]

    print(f"🎯 Returning {len(filtered)} filtered matches (fallback={'yes' if fallback_used else 'no'})")

    # 5) optional: log the search
    _log_search_event(
        {
            "industry": industry,
            "expertise": expertise,
            "availability": availability,
            "min_experience": int(min_experience) if min_experience else 0,
            "max_salary": int(max_salary) if max_salary else 0,
            "location": location,
        },
        results_count=len(filtered),
        fallback_used=fallback_used,
    )

    # 6) return the single best match to the API
    best = filtered[0] if filtered else None
    if best:
        return {
            "id": best["id"],
            "name": best["name"],
            "role": best["role"],
            "industry": sorted(list(best["industries"])) if best["industries"] else [],
            "summary": best["summary"],
            "highlights": best["highlights"],
            "location": best["location"],
        }
    return None
