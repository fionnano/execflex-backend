"""
Candidate sourcing service — People Data Labs (PDL) implementation.

Endpoint: POST https://api.peopledatalabs.com/v5/person/search
Auth:     X-Api-Key header
Docs:     https://docs.peopledatalabs.com/docs/person-search-api

Free tier: 100 person-search calls per month — enough to validate
the pipeline without a paid plan.

Function signatures are kept identical to the older services/apollo_service.py
so routes/roles.py only needs to switch its import. The dedup key in
people_profiles.source_metadata is still `apollo_id` (reused for the
PDL person id) and rows are still written with `source='apollo'` so
existing DB queries, indexes, and the GET /roles/<id>/sourced-candidates
endpoint all continue to work unchanged.
"""
import json
import logging
import os
import threading
import traceback
from typing import Optional

import requests

logger = logging.getLogger(__name__)

PDL_SEARCH_URL = "https://api.peopledatalabs.com/v5/person/search"

# Module-load marker — appears once in Render logs at process start.
# If you post a role and DO NOT see this line above the [SOURCING] logs,
# the running process is importing a stale/cached module.
_MODULE_BUILD_TAG = "sourcing_service@no-from-v3"
print(f"[SOURCING] module loaded: {_MODULE_BUILD_TAG}", flush=True)

# NOTE: PDL_API_KEY is deliberately read inside each function at call time
# via os.environ.get("PDL_API_KEY") — NOT captured at module import time.


# ── Seniority helpers (kept identical to apollo_service.py) ──────────────────

def get_seniority_from_title(title: str) -> list[str]:
    """
    Map a role title to one or more seniority buckets.

    Returns a list because casting wider often gives better results.
    """
    if not title:
        return ["senior", "director"]
    t = title.lower()

    if "chief" in t or "ceo" in t or "coo" in t or "cfo" in t or "cto" in t:
        return ["c_suite", "vp", "director"]
    if "vp" in t or "vice president" in t:
        return ["vp", "c_suite"]
    if "director" in t:
        return ["director", "vp"]
    if "manager" in t:
        return ["manager", "senior"]
    return ["senior", "director"]


def seniority_to_years(title: str) -> int:
    """Estimate years of experience from a job title."""
    if not title:
        return 5
    t = title.lower()
    if "chief" in t or "ceo" in t or "coo" in t or "cfo" in t or "cto" in t:
        return 20
    if "vp" in t or "vice president" in t:
        return 12
    if "director" in t:
        return 8
    if "senior" in t or "lead" in t:
        return 7
    if "manager" in t:
        return 5
    return 5


# ── PDL query builder ────────────────────────────────────────────────────────

_SENIORITY_TO_PDL_LEVEL = {
    "c_suite": "cxo",
    "cxo": "cxo",
    "vp": "vp",
    "director": "director",
    "manager": "manager",
    "senior": "senior",
    "head": "director",
    "partner": "partner",
    "owner": "owner",
}


def build_pdl_query(
    role_title: str,
    location: Optional[str],
    seniority_levels: Optional[list[str]],
) -> str:
    """
    Build a PDL person-search query as a JSON STRING containing an
    Elasticsearch query object. PDL wraps the ES query this way: the
    request body's "query" field must itself be a stringified JSON of
    {"query": {"bool": {...}}}.

    Docs: https://docs.peopledatalabs.com/docs/person-search-api
    """
    must: list[dict] = []

    # Job title — fuzzy match
    if role_title:
        must.append({
            "match": {
                "job_title": {
                    "query": role_title,
                    "fuzziness": "AUTO",
                }
            }
        })

    # Location — product is Ireland-focused, so lock to country=ireland
    # whenever any location string is supplied.
    if location:
        must.append({"term": {"location_country": "ireland"}})

    # Seniority — terms filter (OR semantics over multiple levels)
    pdl_levels = list({
        _SENIORITY_TO_PDL_LEVEL[s.lower()]
        for s in (seniority_levels or [])
        if isinstance(s, str) and s.lower() in _SENIORITY_TO_PDL_LEVEL
    })
    if pdl_levels:
        must.append({"terms": {"job_level": pdl_levels}})

    query_str = json.dumps({"query": {"bool": {"must": must}}})
    print(
        f"[SOURCING] Query type check: {type(query_str).__name__} "
        f"starts_with={query_str[:20]!r}",
        flush=True,
    )
    return query_str


# ── PDL search ───────────────────────────────────────────────────────────────

def search_candidates(
    role_title: str,
    opportunity_id: str,
    location: Optional[str] = None,
    seniority_levels: Optional[list[str]] = None,
    limit: int = 20,
) -> list[dict]:
    """
    Query PDL for matching candidates and return a list of mapped dicts.
    Returns [] on any error or if PDL_API_KEY is not configured.
    """
    api_key = os.environ.get("PDL_API_KEY")
    print(
        f"[SOURCING] search_candidates called: role={role_title!r} "
        f"opportunity={opportunity_id} location={location!r} "
        f"seniorities={seniority_levels} key_present={bool(api_key)}",
        flush=True,
    )
    if not api_key:
        print("[SOURCING] Skipped — no PDL_API_KEY", flush=True)
        return []

    seniorities = seniority_levels or get_seniority_from_title(role_title)
    query = build_pdl_query(role_title, location, seniorities)

    # Fresh dict literal every call — explicitly NO "from" key. PDL dropped
    # offset pagination in favour of scroll_token; we don't need pagination
    # at all since size=20 per call is enough.
    body: dict = {}
    body["query"] = query
    body["size"] = limit
    body["pretty"] = False
    assert "from" not in body, "PDL body accidentally contains 'from' key"

    headers = {
        "Content-Type": "application/json",
        "X-Api-Key": api_key,
    }

    print(
        f"[SOURCING] PDL request body keys={sorted(body.keys())} "
        f"size={body['size']} query_len={len(body['query'])}",
        flush=True,
    )
    print(f"[SOURCING] PDL request body sent: {json.dumps(body)}", flush=True)

    try:
        resp = requests.post(PDL_SEARCH_URL, json=body, headers=headers, timeout=15)
    except requests.Timeout:
        print("[SOURCING] PDL timeout", flush=True)
        return []
    except Exception:
        print(f"[SOURCING] PDL request exception:\n{traceback.format_exc()}", flush=True)
        return []

    if resp.status_code in (401, 403):
        print(f"[SOURCING] PDL auth error (HTTP {resp.status_code}): {resp.text[:300]}", flush=True)
        return []
    if resp.status_code == 402:
        print("[SOURCING] PDL credit limit reached", flush=True)
        return []
    if resp.status_code == 429:
        print("[SOURCING] PDL rate limit", flush=True)
        return []
    if resp.status_code >= 400:
        print(f"[SOURCING] PDL HTTP {resp.status_code}: {resp.text[:500]}", flush=True)
        return []

    try:
        data = resp.json()
    except Exception:
        print(f"[SOURCING] PDL returned non-JSON response:\n{traceback.format_exc()}", flush=True)
        return []

    people = data.get("data") or []
    print(
        f"[SOURCING] PDL raw response: total={data.get('total')} "
        f"returned={len(people)} status={data.get('status')}",
        flush=True,
    )

    return [_map_pdl_person(p, opportunity_id) for p in people if p]


def _map_pdl_person(person: dict, opportunity_id: str) -> dict:
    """Map a single PDL person record into our intermediate candidate dict."""
    first = (person.get("first_name") or "").strip()
    last = (person.get("last_name") or "").strip()
    full_name = (f"{first} {last}").strip() or person.get("full_name") or "Unknown"

    title = person.get("job_title") or ""
    company = person.get("job_company_name")
    headline = title + (f" at {company}" if company else "")

    locality = person.get("location_locality")
    country = person.get("location_country")
    location_parts = [p for p in (locality, country) if p]
    location_str = ", ".join(location_parts).title() if location_parts else None

    linkedin_url = person.get("linkedin_url")
    pdl_id = person.get("id")

    return {
        "name": full_name,
        "headline": headline.strip() or None,
        "location": location_str,
        "years_experience": seniority_to_years(title),
        "linkedin_url": linkedin_url,
        "approved": False,
        "source": "apollo",  # kept for DB compatibility with existing queries
        "source_metadata": {
            "apollo_id": pdl_id,  # reused field for dedup
            "opportunity_id": opportunity_id,
            "has_email": bool(person.get("work_email") or person.get("personal_email")),
            "has_phone": "Yes" if person.get("mobile_phone") or person.get("phone_numbers") else "No",
            "organization_name": company,
            "provider": "pdl",
        },
    }


# ── Upsert + background dispatch (unchanged logic from apollo_service.py) ────

def source_and_upsert(
    opportunity_id: str,
    role_title: str,
    location: Optional[str] = None,
    seniority_levels: Optional[list[str]] = None,
    limit: int = 20,
) -> int:
    """
    Background task: search PDL, upsert results into people_profiles.
    Returns the number of rows written/updated. Never raises.

    Dedup key: source_metadata->>'apollo_id' (reused for PDL person ids
    so the existing schema and indexes continue to work).
    """
    print(
        f"[SOURCING] source_and_upsert started: opportunity={opportunity_id} "
        f"role={role_title!r} location={location!r}",
        flush=True,
    )
    try:
        from config.clients import supabase_client
        if supabase_client is None:
            print("[SOURCING] Upsert skipped — supabase_client unavailable", flush=True)
            return 0

        candidates = search_candidates(
            role_title=role_title,
            opportunity_id=opportunity_id,
            location=location,
            seniority_levels=seniority_levels,
            limit=limit,
        )
        if not candidates:
            print(
                f"[SOURCING] 0 candidates returned for opportunity={opportunity_id} role={role_title!r}",
                flush=True,
            )
            return 0

        written = 0
        skipped_approved = 0
        updated = 0
        inserted = 0

        for cand in candidates:
            apollo_id = (cand.get("source_metadata") or {}).get("apollo_id")
            if not apollo_id:
                continue

            try:
                existing = (
                    supabase_client.table("people_profiles")
                    .select("id, approved, source_metadata")
                    .eq("source_metadata->>apollo_id", str(apollo_id))
                    .limit(1)
                    .execute()
                )

                if existing.data:
                    row = existing.data[0]
                    if row.get("approved") is True:
                        skipped_approved += 1
                        continue

                    sm = row.get("source_metadata") or {}
                    existing_opps = sm.get("opportunity_ids")
                    if isinstance(existing_opps, list):
                        if opportunity_id not in existing_opps:
                            existing_opps.append(opportunity_id)
                    else:
                        prev = sm.get("opportunity_id")
                        existing_opps = [prev] if prev else []
                        if opportunity_id not in existing_opps:
                            existing_opps.append(opportunity_id)

                    sm["opportunity_id"] = opportunity_id
                    sm["opportunity_ids"] = existing_opps
                    sm["apollo_id"] = apollo_id
                    cand_sm = cand.get("source_metadata") or {}
                    sm["has_email"] = cand_sm.get("has_email", sm.get("has_email", False))
                    sm["has_phone"] = cand_sm.get("has_phone", sm.get("has_phone", "No"))
                    sm["organization_name"] = cand_sm.get("organization_name") or sm.get("organization_name")
                    sm["provider"] = cand_sm.get("provider") or sm.get("provider")

                    supabase_client.table("people_profiles").update({
                        "source_metadata": sm,
                    }).eq("id", row["id"]).execute()
                    updated += 1
                    written += 1
                    continue

                # Insert new row — split combined name back into first/last
                full_name = cand.get("name") or ""
                parts = full_name.split(" ", 1)
                first_name = parts[0] if parts else None
                last_name = parts[1] if len(parts) > 1 else None

                row_payload = {
                    "first_name": first_name,
                    "last_name": last_name,
                    "headline": cand.get("headline"),
                    "location": cand.get("location"),
                    "years_experience": cand.get("years_experience"),
                    "linkedin_url": cand.get("linkedin_url"),
                    "approved": False,
                    "source": "apollo",
                    "source_metadata": cand.get("source_metadata") or {},
                }
                supabase_client.table("people_profiles").insert(row_payload).execute()
                inserted += 1
                written += 1
            except Exception as e:
                print(
                    f"[SOURCING] Upsert failed for apollo_id={apollo_id}: {e}\n"
                    f"{traceback.format_exc()}",
                    flush=True,
                )
                continue

        print(
            f"[SOURCING] DONE opportunity={opportunity_id} role={role_title!r} "
            f"sourced={len(candidates)} inserted={inserted} updated={updated} "
            f"skipped_approved={skipped_approved}",
            flush=True,
        )
        return written
    except Exception as e:
        print(
            f"[SOURCING] source_and_upsert top-level error: {e}\n"
            f"{traceback.format_exc()}",
            flush=True,
        )
        return 0


def _thread_target(
    opportunity_id: str,
    role_title: str,
    location: Optional[str],
    seniority_levels: Optional[list[str]],
    limit: int,
) -> None:
    """Wrapped thread entry point; any exception is logged with full traceback."""
    print(
        f"[SOURCING] Thread started for opportunity={opportunity_id}",
        flush=True,
    )
    try:
        source_and_upsert(
            opportunity_id=opportunity_id,
            role_title=role_title,
            location=location,
            seniority_levels=seniority_levels,
            limit=limit,
        )
    except Exception as e:
        print(
            f"[SOURCING] Thread top-level exception: {e}\n{traceback.format_exc()}",
            flush=True,
        )


def source_and_upsert_async(
    opportunity_id: str,
    role_title: str,
    location: Optional[str] = None,
    seniority_levels: Optional[list[str]] = None,
    limit: int = 20,
) -> None:
    """Fire-and-forget background sourcing — never blocks the caller."""
    print(
        f"[SOURCING] source_and_upsert_async dispatching thread for "
        f"opportunity={opportunity_id} role={role_title!r}",
        flush=True,
    )
    threading.Thread(
        target=_thread_target,
        kwargs={
            "opportunity_id": opportunity_id,
            "role_title": role_title,
            "location": location,
            "seniority_levels": seniority_levels,
            "limit": limit,
        },
        daemon=True,
        name=f"sourcing-{opportunity_id[:8]}",
    ).start()
