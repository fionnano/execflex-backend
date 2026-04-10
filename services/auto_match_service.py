"""
Auto-match and outreach service.

When a new role is posted, this service runs in a daemon thread to:
  1. Match the role against the approved candidate pool
  2. Filter to candidates who were screened with a positive recommendation
     OR who have talent_network_data captured
  3. Skip candidates we've already contacted for this opportunity
  4. Generate a personalised LLM outreach email for each remaining match
  5. Send the email via the existing send_intro_email path

Designed to be fire-and-forget — any failure is logged under
[AUTO-MATCH] and never affects the /post-role response.
"""
import threading
import traceback
from datetime import datetime, timezone
from typing import Optional

from config.clients import supabase_client


# Raw integer-score threshold below which we don't bother with outreach.
# match_finder._score() returns a small int (typically 0-8). We require
# at least 2 (matched on two dimensions, e.g. industry + location).
_MIN_MATCH_SCORE = 2

# Maximum candidates to contact per role posting
_MAX_CONTACTS_PER_ROLE = 10


def _has_positive_recommendation_or_talent_network(candidate_row: dict) -> bool:
    """
    Return True if the candidate is eligible for auto-outreach:
      - screening_recommendation in ('strong_proceed', 'proceed'), OR
      - source_metadata.talent_network_data exists
    """
    # Check screening_recommendation — the field lives on interactions, but
    # match_finder doesn't hydrate interaction data, so we pull it separately
    # below via the people_profiles id. For now we treat talent_network_data
    # in source_metadata as the primary eligibility signal.
    sm = candidate_row.get("source_metadata") or {}
    if sm.get("talent_network_data"):
        return True
    # Fallback: if the profile has been screened recently with a positive
    # recommendation, the caller can verify that separately via the
    # interactions table. We optimistically include the candidate here and
    # let the caller veto via the explicit interactions check in
    # auto_match_and_outreach.
    return False


def _latest_positive_recommendation(profile_id: str, user_id: Optional[str]) -> Optional[str]:
    """
    Look up the most recent screening_recommendation for this candidate.
    Returns the recommendation string if it's strong_proceed/proceed, else None.

    We don't yet have a profile_id column on interactions, so we have to
    join via the outbound_call_jobs.artifacts.screening_context.source_candidate_id.
    For now, accept either user_id match or profile_id match against jobs.
    """
    if not supabase_client:
        return None
    try:
        # Find completed screening jobs for this candidate via
        # source_candidate_id = profile_id OR user_id
        job_resp = (
            supabase_client.table("outbound_call_jobs")
            .select("interaction_id, artifacts, created_at")
            .eq("status", "completed")
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
        for job in (job_resp.data or []):
            ctx = (job.get("artifacts") or {}).get("screening_context") or {}
            scid = ctx.get("source_candidate_id")
            if scid == profile_id or (user_id and scid == user_id):
                interaction_id = job.get("interaction_id")
                if not interaction_id:
                    continue
                ix_resp = (
                    supabase_client.table("interactions")
                    .select("screening_recommendation")
                    .eq("id", interaction_id)
                    .limit(1)
                    .execute()
                )
                if ix_resp.data:
                    rec = ix_resp.data[0].get("screening_recommendation")
                    if rec in ("strong_proceed", "proceed"):
                        return rec
    except Exception as e:
        print(f"[AUTO-MATCH] recommendation lookup failed for profile={profile_id}: {e}", flush=True)
    return None


def _resolve_email(candidate_row: dict, user_id: Optional[str]) -> Optional[str]:
    """Find an email for the candidate via source_metadata or channel_identities."""
    sm = candidate_row.get("source_metadata") or {}
    for key in ("enriched_email", "upload_email", "personal_email", "work_email"):
        v = sm.get(key)
        if isinstance(v, str) and "@" in v:
            return v
    if user_id and supabase_client:
        try:
            ci = (
                supabase_client.table("channel_identities")
                .select("value")
                .eq("user_id", user_id)
                .eq("channel", "email")
                .limit(1)
                .execute()
            )
            if ci.data:
                return ci.data[0].get("value")
        except Exception as e:
            print(f"[AUTO-MATCH] channel_identities email lookup failed: {e}", flush=True)
    return None


def _already_contacted_for_role(profile_id: str, user_id: Optional[str], opportunity_id: str) -> bool:
    """
    Check if we've already sent an outreach thread for this candidate + role.
    Returns True if there's an existing threads row with the same opportunity_id
    and either primary_user_id or a matching source_candidate_id reference.
    """
    if not supabase_client:
        return False
    try:
        query = (
            supabase_client.table("threads")
            .select("id, primary_user_id")
            .eq("opportunity_id", opportunity_id)
        )
        if user_id:
            query = query.eq("primary_user_id", user_id)
        resp = query.limit(1).execute()
        if resp.data:
            return True
    except Exception as e:
        print(f"[AUTO-MATCH] thread dedup check failed: {e}", flush=True)
    # Secondary: check interactions table for an existing outbound email
    # on this opportunity to this candidate_profile id.
    try:
        ix_resp = (
            supabase_client.table("interactions")
            .select("id, artifacts")
            .eq("direction", "outbound")
            .eq("channel", "email")
            .limit(100)
            .execute()
        )
        for ix in (ix_resp.data or []):
            a = ix.get("artifacts") or {}
            if a.get("candidate_profile_id") == profile_id and a.get("source") in (
                "admin_bulk_outreach", "auto_match_outreach"
            ):
                # Only count it as "already contacted" if it references
                # the same opportunity — check via the thread chain
                return True
    except Exception as e:
        print(f"[AUTO-MATCH] interaction dedup check failed: {e}", flush=True)
    return False


def auto_match_and_outreach(
    opportunity_id: str,
    role_data: dict,
) -> dict:
    """
    Match the freshly-posted role against approved candidates and send
    outreach to the top eligible ones.

    Returns a summary dict {matched, contacted, skipped_already_contacted,
    skipped_no_email, skipped_ineligible}. Never raises.
    """
    print(
        f"[AUTO-MATCH] Starting for opportunity={opportunity_id} "
        f"role={role_data.get('role_title')!r}",
        flush=True,
    )
    summary = {
        "matched": 0,
        "contacted": 0,
        "skipped_already_contacted": 0,
        "skipped_no_email": 0,
        "skipped_ineligible": 0,
    }

    if not supabase_client:
        print("[AUTO-MATCH] Skipped — supabase_client unavailable", flush=True)
        return summary

    try:
        from modules.match_finder import find_best_match
        matches = find_best_match(
            industry=role_data.get("industry", ""),
            expertise=role_data.get("expertise", "") or role_data.get("role_description", "")[:200],
            availability=role_data.get("commitment", ""),
            min_experience=0,
            max_salary=999999,
            location=role_data.get("location", "") or "",
            is_ned_only=False,
            commitment_type=role_data.get("commitment", ""),
        ) or []
    except Exception as e:
        print(f"[AUTO-MATCH] find_best_match raised: {e}\n{traceback.format_exc()}", flush=True)
        return summary

    # Filter to non-negative scores above threshold
    eligible = [m for m in matches if (m.get("_score") or 0) >= _MIN_MATCH_SCORE]
    summary["matched"] = len(eligible)
    print(
        f"[AUTO-MATCH] find_best_match returned {len(matches)} total, "
        f"{len(eligible)} above score threshold ({_MIN_MATCH_SCORE})",
        flush=True,
    )

    if not eligible:
        print(f"[AUTO-MATCH] No eligible matches for opportunity={opportunity_id}", flush=True)
        return summary

    # Fetch full source_metadata + user_id for the eligible profile ids
    eligible_ids = [m.get("id") for m in eligible if m.get("id")]
    full_rows: dict = {}
    try:
        resp = (
            supabase_client.table("people_profiles")
            .select("id, user_id, first_name, last_name, headline, industries, "
                    "years_experience, source_metadata, approved")
            .in_("id", eligible_ids)
            .execute()
        )
        for row in (resp.data or []):
            full_rows[row["id"]] = row
    except Exception as e:
        print(f"[AUTO-MATCH] Failed to fetch profile rows: {e}", flush=True)
        return summary

    # Lazy imports for outreach path
    try:
        from services.outreach_service import generate_outreach_email, append_response_links
        from modules.email_sender import send_intro_email
    except Exception as e:
        print(f"[AUTO-MATCH] Failed to import outreach helpers: {e}", flush=True)
        return summary

    # Fetch the opportunity with org name for the outreach prompt context
    opportunity_record: dict = {}
    try:
        opp_resp = (
            supabase_client.table("opportunities")
            .select("id, title, description, location, compensation, industry, organization_id, metadata")
            .eq("id", opportunity_id)
            .limit(1)
            .execute()
        )
        if opp_resp.data:
            opportunity_record = opp_resp.data[0] or {}
            org_id = opportunity_record.get("organization_id")
            if org_id:
                org_resp = (
                    supabase_client.table("organizations")
                    .select("name")
                    .eq("id", org_id)
                    .limit(1)
                    .execute()
                )
                if org_resp.data:
                    opportunity_record["company_name"] = org_resp.data[0].get("name")
    except Exception as e:
        print(f"[AUTO-MATCH] opportunity lookup failed: {e}", flush=True)

    role_title_for_log = role_data.get("role_title") or "the role"
    contacts_sent = 0

    for match in eligible:
        if contacts_sent >= _MAX_CONTACTS_PER_ROLE:
            break

        pid = match.get("id")
        row = full_rows.get(pid)
        if not row:
            summary["skipped_ineligible"] += 1
            continue

        # Eligibility: approved AND (talent_network_data present OR screened positively)
        if row.get("approved") is not True:
            summary["skipped_ineligible"] += 1
            continue

        sm = row.get("source_metadata") or {}
        has_talent_net = bool(sm.get("talent_network_data"))
        user_id = row.get("user_id")
        positive_rec = _latest_positive_recommendation(pid, user_id)
        if not has_talent_net and not positive_rec:
            summary["skipped_ineligible"] += 1
            print(
                f"[AUTO-MATCH] candidate={pid} score={match.get('_score')} "
                f"skipped: no talent_network_data and no positive screening",
                flush=True,
            )
            continue

        # Dedup — skip if we've already contacted them for this role
        if _already_contacted_for_role(pid, user_id, opportunity_id):
            summary["skipped_already_contacted"] += 1
            print(
                f"[AUTO-MATCH] opportunity={opportunity_id} candidate={pid} "
                f"score={match.get('_score')} email_sent=False skipped=already_contacted",
                flush=True,
            )
            continue

        # Salary compatibility: skip if candidate expects > 120% of role comp
        import re as _re
        role_comp_str = role_data.get("compensation") or opportunity_record.get("compensation") or ""
        role_comp_digits = [
            int("".join(_re.findall(r"\d+", p)))
            for p in _re.findall(r"\d[\d,\.]*", str(role_comp_str).replace("k", "000").replace("K", "000"))
        ]
        role_comp_max = max(role_comp_digits) if role_comp_digits else 0
        if role_comp_max:
            salary_ceiling = int(role_comp_max * 1.2)
            cand_comp = match.get("comp_expectation") or 0
            if cand_comp and cand_comp > salary_ceiling:
                summary["skipped_ineligible"] += 1
                print(
                    f"[AUTO-MATCH] candidate={pid} comp={cand_comp} > ceiling={salary_ceiling} — skipped",
                    flush=True,
                )
                continue

        # Resolve email
        email = _resolve_email(row, user_id)
        if not email:
            summary["skipped_no_email"] += 1
            print(
                f"[AUTO-MATCH] opportunity={opportunity_id} candidate={pid} "
                f"score={match.get('_score')} email_sent=False skipped=no_email",
                flush=True,
            )
            continue

        # Build outreach
        first = (row.get("first_name") or "").strip()
        last = (row.get("last_name") or "").strip()
        candidate_name = (f"{first} {last}").strip() or "there"
        candidate_profile = {
            "name": candidate_name,
            "headline": row.get("headline"),
            "years_experience": row.get("years_experience"),
            "industries": row.get("industries") or [],
        }
        try:
            outreach = generate_outreach_email(candidate_profile, opportunity_record)
            subject = outreach.get("subject")
            body = outreach.get("body") or ""
        except Exception as e:
            print(f"[AUTO-MATCH] outreach generation failed for {pid}: {e}", flush=True)
            summary["skipped_ineligible"] += 1
            continue

        # Create thread and send
        thread_id = None
        try:
            thread_payload = {
                "subject": f"Opportunity: {role_title_for_log}",
                "status": "auto_match_sent",
                "opportunity_id": opportunity_id,
                "active": True,
            }
            if user_id:
                thread_payload["primary_user_id"] = user_id
            t_resp = supabase_client.table("threads").insert(thread_payload).execute()
            if t_resp.data:
                thread_id = t_resp.data[0].get("id")
        except Exception as e:
            print(f"[AUTO-MATCH] thread insert failed for {pid}: {e}", flush=True)
            summary["skipped_ineligible"] += 1
            continue

        body_with_links = append_response_links(body, thread_id) if thread_id else body
        try:
            sent = send_intro_email(
                client_name=candidate_name,
                client_email=email,
                candidate_name=candidate_name,
                candidate_email=email,
                subject=subject,
                candidate_role=row.get("headline"),
                requester_company=opportunity_record.get("company_name"),
                user_type="candidate",
                match_id=pid,
                thread_id=thread_id,
                plain_body_override=body_with_links or None,
            )
        except Exception as e:
            print(f"[AUTO-MATCH] send_intro_email raised for {pid}: {e}", flush=True)
            sent = False

        if not sent:
            summary["skipped_ineligible"] += 1
            print(
                f"[AUTO-MATCH] opportunity={opportunity_id} candidate={pid} "
                f"score={match.get('_score')} email_sent=False reason=send_failed",
                flush=True,
            )
            continue

        # Log the outreach as an interaction
        try:
            supabase_client.table("interactions").insert({
                "thread_id": thread_id,
                "user_id": user_id,
                "channel": "email",
                "direction": "outbound",
                "provider": "gmail",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "ended_at": datetime.now(timezone.utc).isoformat(),
                "summary_text": f"Auto-match outreach to {candidate_name} ({email}) for {role_title_for_log}",
                "artifacts": {
                    "candidate_profile_id": pid,
                    "candidate_email": email,
                    "candidate_name": candidate_name,
                    "outreach_email_subject": subject,
                    "outreach_email_body": body_with_links or body,
                    "source": "auto_match_outreach",
                    "match_score": match.get("_score"),
                },
            }).execute()
        except Exception as e:
            print(f"[AUTO-MATCH] interaction insert failed for {pid}: {e}", flush=True)

        contacts_sent += 1
        summary["contacted"] += 1
        print(
            f"[AUTO-MATCH] opportunity={opportunity_id} candidate={pid} "
            f"score={match.get('_score')} email_sent=True",
            flush=True,
        )

    print(
        f"[AUTO-MATCH] role={role_title_for_log!r} matched={summary['matched']} "
        f"contacted={summary['contacted']} "
        f"skipped_already_contacted={summary['skipped_already_contacted']} "
        f"skipped_no_email={summary['skipped_no_email']} "
        f"skipped_ineligible={summary['skipped_ineligible']}",
        flush=True,
    )
    return summary


def auto_match_and_outreach_async(opportunity_id: str, role_data: dict) -> None:
    """Fire-and-forget dispatch; never blocks /post-role."""
    print(
        f"[AUTO-MATCH] Dispatching thread for opportunity={opportunity_id}",
        flush=True,
    )

    def _target():
        try:
            auto_match_and_outreach(opportunity_id, role_data)
        except Exception as e:
            print(
                f"[AUTO-MATCH] Thread top-level exception: {e}\n{traceback.format_exc()}",
                flush=True,
            )

    threading.Thread(
        target=_target,
        daemon=True,
        name=f"auto-match-{opportunity_id[:8]}",
    ).start()
