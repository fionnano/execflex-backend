"""
Candidate erasure (GDPR-E-002, Art. 17).

The only deletion routine used to key on ``user_id``, so PDL-sourced
prospects, CSV-uploaded candidates and talent-network opt-ins (none of
which have a user) could not be erased, and transcripts, scores and the
candidate portal token stayed behind as "append-only history".

``erase_candidate`` takes any of profile id, user id, e-mail or phone,
expands to every identifier we hold for that person, and then:

  people_profiles             deleted (fallback: anonymised in place)
  channel_identities          deleted
  outbound_call_jobs          deleted (phone, screening_context, debug events)
  interaction_turns           deleted
  interactions                scrubbed: transcript, scores, summary,
                              candidate_token, raw_payload, artifacts
  screening_feedback_requests deleted
  threads                     deactivated
  contact_suppressions        a row added so the person is not re-sourced

Every step is best-effort and reported; the caller stores the report as
the completion record. Third-party processors (OpenAI, PostHog, Stripe)
are listed in the report under ``manual_steps`` because they have no API
hook here.
"""
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from services.compliance.suppression import normalize_email, normalize_phone, suppress
from utils.log_redaction import mask_email, mask_phone

PROFILE_EMAIL_KEYS = ("upload_email", "enriched_email", "personal_email", "work_email")
PROFILE_PHONE_KEYS = ("upload_phone", "enriched_phone")
INTERACTION_SCRUB = {
    "transcript_text": None,
    "screening_scores": None,
    "screening_recommendation": None,
    "summary_text": None,
    "candidate_token": None,
    "raw_payload": None,
}
MANUAL_STEPS = [
    "OpenAI: transcripts/audio sent to the Realtime and chat APIs are covered by "
    "OpenAI's API data retention (30 days abuse monitoring); no per-subject API.",
    "PostHog: delete the person by distinct_id (profile/user id) in the PostHog UI.",
    "Stripe: only organisation owners have customer records; not applicable to candidates.",
    "Render logs: PII is no longer logged (GDPR-E-003); older log lines expire with retention.",
]


def _client():
    from config.clients import supabase_client
    return supabase_client


def _rows(resp) -> List[Dict]:
    return list(getattr(resp, "data", None) or [])


class _Report:
    def __init__(self):
        self.deleted: Dict[str, int] = {}
        self.scrubbed: Dict[str, int] = {}
        self.warnings: List[str] = []

    def bump(self, bucket: Dict[str, int], table: str, n: int = 1):
        bucket[table] = bucket.get(table, 0) + n

    def as_dict(self, identifiers: Dict) -> Dict:
        return {
            "erased_at": datetime.now(timezone.utc).isoformat(),
            "identifiers": identifiers,
            "deleted": self.deleted,
            "scrubbed": self.scrubbed,
            "warnings": self.warnings,
            "manual_steps": MANUAL_STEPS,
        }


def _find_profiles(sb, profile_id, user_id, emails: Set[str], phones: Set[str]) -> List[Dict]:
    found: Dict[str, Dict] = {}

    def add(resp):
        for r in _rows(resp):
            if r.get("id"):
                found[r["id"]] = r

    select = "id, user_id, source_metadata"
    try:
        if profile_id:
            add(sb.table("people_profiles").select(select).eq("id", profile_id).execute())
        if user_id:
            add(sb.table("people_profiles").select(select).eq("user_id", user_id).execute())
        for email in emails:
            for key in PROFILE_EMAIL_KEYS:
                add(sb.table("people_profiles").select(select)
                    .eq(f"source_metadata->>{key}", email).execute())
        for phone in phones:
            for key in PROFILE_PHONE_KEYS:
                add(sb.table("people_profiles").select(select)
                    .eq(f"source_metadata->>{key}", phone).execute())
    except Exception as e:
        print(f"[ERASURE] profile lookup failed: {e}", flush=True)
    return list(found.values())


def erase_candidate(
    profile_id: Optional[str] = None,
    user_id: Optional[str] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    reason: str = "erasure_request",
    source: str = "data_rights",
) -> Dict:
    """Erase everything held about one person. Returns a completion report."""
    sb = _client()
    emails: Set[str] = set(filter(None, [normalize_email(email)]))
    phones: Set[str] = set(filter(None, [normalize_phone(phone)]))
    user_ids: Set[str] = set(filter(None, [user_id]))
    profile_ids: Set[str] = set(filter(None, [profile_id]))
    report = _Report()

    if not sb:
        report.warnings.append("database unavailable")
        return report.as_dict({"emails": sorted(emails), "phones": sorted(phones)})

    # 1. Expand identifiers: channel identities give user ids and the other
    #    contact value; profiles give user ids and uploaded/enriched values.
    for channel, values in (("email", list(emails)), ("phone", list(phones))):
        for value in values:
            try:
                for ci in _rows(sb.table("channel_identities").select("user_id, channel, value")
                                .eq("channel", channel).eq("value", value).execute()):
                    if ci.get("user_id"):
                        user_ids.add(ci["user_id"])
            except Exception as e:
                report.warnings.append(f"channel_identities lookup ({channel}): {e}")
    for uid in list(user_ids):
        try:
            for ci in _rows(sb.table("channel_identities").select("channel, value")
                            .eq("user_id", uid).execute()):
                if ci.get("channel") == "email" and normalize_email(ci.get("value")):
                    emails.add(normalize_email(ci["value"]))
                if ci.get("channel") == "phone" and normalize_phone(ci.get("value")):
                    phones.add(normalize_phone(ci["value"]))
        except Exception as e:
            report.warnings.append(f"channel_identities expand: {e}")

    profiles = _find_profiles(sb, profile_id, user_id, emails, phones)
    for p in profiles:
        profile_ids.add(p["id"])
        if p.get("user_id"):
            user_ids.add(p["user_id"])
        sm = p.get("source_metadata") or {}
        for key in PROFILE_EMAIL_KEYS:
            if normalize_email(sm.get(key)):
                emails.add(normalize_email(sm[key]))
        for key in PROFILE_PHONE_KEYS:
            if normalize_phone(sm.get(key)):
                phones.add(normalize_phone(sm[key]))

    identifiers = {
        "profile_ids": sorted(profile_ids),
        "user_ids": sorted(user_ids),
        "emails": sorted(emails),
        "phones": sorted(phones),
    }
    print(
        f"[ERASURE] start profiles={len(profile_ids)} users={len(user_ids)} "
        f"emails={[mask_email(e) for e in emails]} phones={[mask_phone(p) for p in phones]}",
        flush=True,
    )

    # 2. Call jobs by phone and by user: collect interactions, delete jobs.
    jobs: Dict[str, Dict] = {}
    try:
        for ph in phones:
            for j in _rows(sb.table("outbound_call_jobs").select("id, interaction_id")
                           .eq("phone_e164", ph).execute()):
                jobs[j["id"]] = j
        for uid in user_ids:
            for j in _rows(sb.table("outbound_call_jobs").select("id, interaction_id")
                           .eq("user_id", uid).execute()):
                jobs[j["id"]] = j
    except Exception as e:
        report.warnings.append(f"outbound_call_jobs lookup: {e}")
    interaction_ids = {j["interaction_id"] for j in jobs.values() if j.get("interaction_id")}

    for job_id in jobs:
        try:
            sb.table("outbound_call_jobs").delete().eq("id", job_id).execute()
            report.bump(report.deleted, "outbound_call_jobs")
        except Exception as e:
            report.warnings.append(f"outbound_call_jobs {job_id}: {e}")

    # 3. Transcript turns are deleted; interaction rows are scrubbed in place
    #    (the table is append-only by design, so the row stays, empty).
    for ix_id in interaction_ids:
        try:
            sb.table("interaction_turns").delete().eq("interaction_id", ix_id).execute()
            report.bump(report.deleted, "interaction_turns")
        except Exception as e:
            report.warnings.append(f"interaction_turns {ix_id}: {e}")
        try:
            scrub = dict(INTERACTION_SCRUB)
            scrub["artifacts"] = {"erased_at": datetime.now(timezone.utc).isoformat(), "reason": reason}
            sb.table("interactions").update(scrub).eq("id", ix_id).execute()
            report.bump(report.scrubbed, "interactions")
        except Exception as e:
            report.warnings.append(f"interactions {ix_id}: {e}")

    # 4. Feedback requests keyed on the contact values.
    for column, values in (("candidate_email", emails), ("candidate_phone", phones)):
        for value in values:
            try:
                sb.table("screening_feedback_requests").delete().eq(column, value).execute()
                report.bump(report.deleted, "screening_feedback_requests")
            except Exception as e:
                report.warnings.append(f"screening_feedback_requests {column}: {e}")

    # 5. Identity and profile rows.
    for uid in user_ids:
        for table in ("channel_identities", "user_preferences", "linkedin_connections"):
            try:
                sb.table(table).delete().eq("user_id", uid).execute()
                report.bump(report.deleted, table)
            except Exception as e:
                if "does not exist" not in str(e).lower():
                    report.warnings.append(f"{table} {uid}: {e}")
        try:
            sb.table("threads").update({"active": False}).eq("primary_user_id", uid).execute()
            report.bump(report.scrubbed, "threads")
        except Exception as e:
            report.warnings.append(f"threads {uid}: {e}")
    for column, values in (("email", emails), ("phone", phones)):
        for value in values:
            try:
                sb.table("channel_identities").delete().eq("channel", column).eq("value", value).execute()
                report.bump(report.deleted, "channel_identities")
            except Exception as e:
                report.warnings.append(f"channel_identities {column}: {e}")

    for pid in profile_ids:
        try:
            sb.table("people_profiles").delete().eq("id", pid).execute()
            report.bump(report.deleted, "people_profiles")
        except Exception as e:
            # FK or trigger in the way: anonymise instead so nothing personal remains.
            try:
                sb.table("people_profiles").update({
                    "first_name": None, "last_name": None, "headline": None,
                    "bio": None, "location": None, "linkedin_url": None,
                    "source_metadata": {"erased_at": datetime.now(timezone.utc).isoformat()},
                    "approved": False,
                }).eq("id", pid).execute()
                report.bump(report.scrubbed, "people_profiles")
                report.warnings.append(f"people_profiles {pid}: delete failed ({e}); anonymised instead")
            except Exception as e2:
                report.warnings.append(f"people_profiles {pid}: {e2}")

    # 6. Never source or contact this person again.
    for value in emails:
        suppress(email=value, reason=reason, source=source)
    for value in phones:
        suppress(phone=value, reason=reason, source=source)

    result = report.as_dict(identifiers)
    print(
        f"[ERASURE] done deleted={report.deleted} scrubbed={report.scrubbed} "
        f"warnings={len(report.warnings)}",
        flush=True,
    )
    return result


def export_candidate(email: Optional[str] = None, phone: Optional[str] = None,
                     profile_id: Optional[str] = None) -> Dict:
    """Art. 15 access: everything we hold, keyed the same way as erasure."""
    sb = _client()
    out: Dict = {"profiles": [], "interactions": [], "feedback_requests": []}
    if not sb:
        return out
    emails = set(filter(None, [normalize_email(email)]))
    phones = set(filter(None, [normalize_phone(phone)]))
    profiles = _find_profiles(sb, profile_id, None, emails, phones)
    out["profiles"] = profiles
    try:
        for ph in phones:
            for j in _rows(sb.table("outbound_call_jobs").select("id, interaction_id, artifacts")
                           .eq("phone_e164", ph).execute()):
                if j.get("interaction_id"):
                    ix = _rows(sb.table("interactions")
                               .select("id, transcript_text, screening_scores, screening_recommendation, summary_text")
                               .eq("id", j["interaction_id"]).execute())
                    out["interactions"].extend(ix)
        for value in emails:
            out["feedback_requests"].extend(_rows(
                sb.table("screening_feedback_requests").select("*").eq("candidate_email", value).execute()))
    except Exception as e:
        out["warning"] = str(e)
    return out
