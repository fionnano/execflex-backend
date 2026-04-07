"""
Apollo.io candidate sourcing service.

Endpoint: POST https://api.apollo.io/api/v1/mixed_people/api_search
Auth:     api_key passed in the JSON body (not as a header).

Notes:
- Last names in search results are obfuscated (e.g. "Hu***n").
- No emails or phones are returned from search; enrichment requires
  a separate paid endpoint and is intentionally NOT performed here.
- This endpoint does not consume credits.
"""
import logging
import os
import threading
from typing import Optional

import requests

logger = logging.getLogger(__name__)

APOLLO_SEARCH_URL = "https://api.apollo.io/api/v1/mixed_people/api_search"

# NOTE: APOLLO_API_KEY is deliberately read inside each function at call time
# via os.environ.get("APOLLO_API_KEY") — NOT captured at module import time.
# Capturing at import would latch a None if the env var wasn't set when the
# module first loaded. Reading per-call is slightly more overhead but avoids
# a whole class of "the key is set but nothing happens" bugs on Render.


# ── Seniority helpers ────────────────────────────────────────────────────────

def get_seniority_from_title(title: str) -> list[str]:
    """
    Map a role title to one or more Apollo seniority buckets.

    Returns a list because Apollo accepts multiple seniorities and
    casting wider often gives better results.
    """
    if not title:
        return ["senior", "director"]
    t = title.lower()

    if "chief" in t or "ceo" in t or "coo" in t or "cfo" in t or "cto" in t:
        return ["c_suite"]
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


# ── Apollo search ────────────────────────────────────────────────────────────

def search_candidates(
    role_title: str,
    opportunity_id: str,
    location: Optional[str] = None,
    seniority_levels: Optional[list[str]] = None,
    limit: int = 20,
) -> list[dict]:
    """
    Query Apollo for matching candidates and return a list of mapped dicts.
    Returns [] on any error or if APOLLO_API_KEY is not configured.
    """
    # Read env var at CALL TIME, not import time — see top-of-file note.
    api_key = os.environ.get("APOLLO_API_KEY")
    print(
        f"[APOLLO] search_candidates called: role={role_title!r} "
        f"opportunity={opportunity_id} location={location!r} "
        f"seniorities={seniority_levels} key_present={bool(api_key)}",
        flush=True,
    )
    if not api_key:
        print("[APOLLO] Sourcing skipped — APOLLO_API_KEY not set in environment", flush=True)
        return []

    body = {
        "person_titles": [role_title],
        "person_locations": [location] if location else [],
        "person_seniorities": seniority_levels or get_seniority_from_title(role_title),
        "per_page": limit,
        "page": 1,
    }
    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "accept": "application/json",
        "X-Api-Key": api_key,
    }

    try:
        resp = requests.post(APOLLO_SEARCH_URL, json=body, headers=headers, timeout=10)
    except requests.Timeout:
        logger.warning("Apollo request timed out")
        return []
    except Exception:
        logger.exception("Apollo sourcing error")
        return []

    if resp.status_code == 401:
        logger.error("Apollo API key invalid")
        return []
    if resp.status_code == 429:
        logger.warning("Apollo rate limit hit — sourcing skipped")
        return []
    if resp.status_code >= 400:
        logger.error(f"Apollo HTTP {resp.status_code}: {resp.text[:300]}")
        return []

    try:
        data = resp.json()
    except Exception:
        logger.exception("Apollo returned non-JSON response")
        return []

    people = data.get("people") or []
    return [_map_person(p, opportunity_id) for p in people if p]


def _map_person(person: dict, opportunity_id: str) -> dict:
    """Map an Apollo person record to our intermediate dict shape."""
    first = person.get("first_name") or ""
    last = person.get("last_name_obfuscated") or person.get("last_name") or ""
    name = (f"{first} {last}").strip() or person.get("name") or "Unknown"

    title = person.get("title") or ""
    org = person.get("organization") or {}
    org_name = org.get("name") if isinstance(org, dict) else None
    headline = title + (f" at {org_name}" if org_name else "")

    return {
        "name": name,
        "headline": headline.strip() or None,
        "location": None,
        "years_experience": seniority_to_years(title),
        "approved": False,
        "source": "apollo",
        "source_metadata": {
            "apollo_id": person.get("id"),
            "opportunity_id": opportunity_id,
            "has_email": person.get("has_email", False),
            "has_phone": person.get("has_direct_phone", "No"),
            "organization_name": org_name,
        },
    }


# ── Background sourcing entry point (used by /post-role) ────────────────────

def source_and_upsert(
    opportunity_id: str,
    role_title: str,
    location: Optional[str] = None,
    seniority_levels: Optional[list[str]] = None,
    limit: int = 20,
) -> int:
    """
    Background task: search Apollo, upsert results into people_profiles.
    Returns the number of rows written/updated. Never raises.
    """
    print(
        f"[APOLLO] source_and_upsert started: opportunity={opportunity_id} "
        f"role={role_title!r} location={location!r}",
        flush=True,
    )
    try:
        from config.clients import supabase_client
        if supabase_client is None:
            print("[APOLLO] Upsert skipped — supabase_client unavailable", flush=True)
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
                f"[APOLLO] 0 candidates returned for opportunity={opportunity_id} role={role_title!r}",
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
                # Dedup by source_metadata->>'apollo_id'
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

                    # Append this opportunity_id to the existing source_metadata
                    sm = row.get("source_metadata") or {}
                    existing_opps = sm.get("opportunity_ids")
                    if isinstance(existing_opps, list):
                        if opportunity_id not in existing_opps:
                            existing_opps.append(opportunity_id)
                    else:
                        # First time we see multiple opportunities for this candidate
                        prev = sm.get("opportunity_id")
                        existing_opps = [prev] if prev else []
                        if opportunity_id not in existing_opps:
                            existing_opps.append(opportunity_id)

                    sm["opportunity_id"] = opportunity_id  # most-recent
                    sm["opportunity_ids"] = existing_opps
                    sm["apollo_id"] = apollo_id  # ensure preserved
                    sm["has_email"] = cand["source_metadata"].get("has_email", sm.get("has_email", False))
                    sm["has_phone"] = cand["source_metadata"].get("has_phone", sm.get("has_phone", "No"))
                    sm["organization_name"] = cand["source_metadata"].get("organization_name") or sm.get("organization_name")

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
                    "approved": False,
                    "source": "apollo",
                    "source_metadata": cand.get("source_metadata") or {},
                }
                supabase_client.table("people_profiles").insert(row_payload).execute()
                inserted += 1
                written += 1
            except Exception as e:
                logger.warning(f"Apollo upsert failed for apollo_id={apollo_id}: {e}")
                continue

        print(
            f"[APOLLO] DONE opportunity={opportunity_id} role={role_title!r} "
            f"sourced={len(candidates)} inserted={inserted} updated={updated} "
            f"skipped_approved={skipped_approved}",
            flush=True,
        )
        return written
    except Exception as e:
        print(f"[APOLLO] source_and_upsert top-level error: {e}", flush=True)
        logger.exception("Apollo source_and_upsert top-level error")
        return 0


def _thread_target(
    opportunity_id: str,
    role_title: str,
    location: Optional[str],
    seniority_levels: Optional[list[str]],
    limit: int,
) -> None:
    """
    Wrapped thread entry point. Logs any top-level exception with traceback
    so it can't be silently lost (threads that raise just vanish otherwise).
    """
    print(
        f"[APOLLO] Thread started for opportunity={opportunity_id}",
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
        import traceback
        print(
            f"[APOLLO] Thread top-level exception: {e}\n{traceback.format_exc()}",
            flush=True,
        )
        logger.exception(f"Apollo thread top-level exception: {e}")


def source_and_upsert_async(
    opportunity_id: str,
    role_title: str,
    location: Optional[str] = None,
    seniority_levels: Optional[list[str]] = None,
    limit: int = 20,
) -> None:
    """Fire-and-forget background sourcing — never blocks the caller."""
    print(
        f"[APOLLO] source_and_upsert_async dispatching thread for "
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
        name=f"apollo-source-{opportunity_id[:8]}",
    ).start()
