"""
LLM-generated outreach email service.

Generates personalised executive-search outreach emails using GPT-4o.
Falls back gracefully to a static template when OpenAI is unavailable.
"""
import json
import os
import traceback
from typing import Optional

from config.clients import gpt_client


_OUTREACH_SYSTEM_PROMPT = (
    "You are an executive search consultant writing a personalised outreach "
    "email to a senior candidate. Warm, direct, professional. Never use "
    "'I came across your profile'. Reference something specific about their "
    "background. Under 150 words. Return JSON only: "
    "{subject: string, body: string}"
)


def _format_candidate(candidate_profile: dict) -> str:
    name = candidate_profile.get("name") or candidate_profile.get("full_name") or "the candidate"
    headline = candidate_profile.get("headline") or candidate_profile.get("job_title") or ""
    years = candidate_profile.get("years_experience")
    years_str = f"{years} years experience" if years else "several years experience"
    return f"{name}, {headline}, {years_str}".strip(", ")


def _format_opportunity(opportunity: dict) -> str:
    title = opportunity.get("title") or opportunity.get("role_title") or "a senior role"
    company = opportunity.get("company_name") or "a fast-growing company"
    location = opportunity.get("location") or "flexible location"
    compensation = opportunity.get("compensation") or opportunity.get("budget_range") or "competitive"
    return f"{title} at {company}, {location}, budget: {compensation}"


def _static_fallback(candidate_profile: dict, opportunity: dict) -> dict:
    """Static template used when GPT-4o is unavailable."""
    name = candidate_profile.get("name") or candidate_profile.get("full_name") or "there"
    first_name = name.split(" ", 1)[0]
    title = opportunity.get("title") or opportunity.get("role_title") or "a senior opportunity"
    company = opportunity.get("company_name") or "a client of ours"
    body = (
        f"Hi {first_name},\n\n"
        f"I'm reaching out about {title} at {company}. Based on your background "
        f"I think this could be a strong fit and worth a short conversation.\n\n"
        f"Would you be open to a quick call this week to discuss?\n\n"
        f"Best regards,\n"
        f"The Ainm Search team"
    )
    subject = f"{title} at {company} — worth a conversation?"
    return {"subject": subject, "body": body}


def generate_outreach_email(
    candidate_profile: dict,
    opportunity: dict,
) -> dict:
    """
    Generate a personalised outreach email via GPT-4o.

    Always returns a dict with {"subject": str, "body": str}.
    Falls back to a static template on any failure or when OPENAI_API_KEY
    is not configured.
    """
    # Fallback path — no GPT client or no API key
    if gpt_client is None or not os.environ.get("OPENAI_API_KEY"):
        print("[OUTREACH] GPT client unavailable — using static fallback", flush=True)
        return _static_fallback(candidate_profile, opportunity)

    user_message = (
        f"Candidate: {_format_candidate(candidate_profile)}\n"
        f"Role: {_format_opportunity(opportunity)}\n"
        f"Write the outreach email."
    )

    try:
        print(
            f"[OUTREACH] GPT-4o call: candidate={candidate_profile.get('name')!r} "
            f"role={opportunity.get('title') or opportunity.get('role_title')!r}",
            flush=True,
        )
        resp = gpt_client.chat.completions.create(
            model="gpt-4o",
            temperature=0.7,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _OUTREACH_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
        )
        content = (resp.choices[0].message.content or "").strip()
        parsed = json.loads(content)
        subject = (parsed.get("subject") or "").strip()
        body = (parsed.get("body") or "").strip()
        if not subject or not body:
            print(f"[OUTREACH] GPT returned incomplete JSON: {content[:200]}", flush=True)
            return _static_fallback(candidate_profile, opportunity)
        print(f"[OUTREACH] GPT-4o generated: subject={subject[:80]!r}", flush=True)
        return {"subject": subject, "body": body}
    except Exception as e:
        print(
            f"[OUTREACH] GPT-4o call failed: {e}\n{traceback.format_exc()}",
            flush=True,
        )
        return _static_fallback(candidate_profile, opportunity)


def append_response_links(body: str, thread_id: str, base_url: Optional[str] = None) -> str:
    """
    Append the interested / not-interested response links to an outreach
    email body. The links point at GET /intro/respond on the ExecFlex
    backend so candidates can self-serve.
    """
    if not thread_id:
        return body
    base = (base_url or os.environ.get("EXECFLEX_BASE_URL") or "https://execflex-backend-1.onrender.com").rstrip("/")
    interested = f"{base}/intro/respond?thread_id={thread_id}&action=interested"
    not_interested = f"{base}/intro/respond?thread_id={thread_id}&action=notinterested"
    footer = (
        "\n\n---\n"
        f"Interested? {interested}\n"
        f"Not right now? {not_interested}"
    )
    return body.rstrip() + footer
