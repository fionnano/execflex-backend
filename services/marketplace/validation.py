"""Input validation for the marketplace public forms.

Every value that reaches the store from an untrusted request body passes through
here. Validators raise ValidationError (mapped to a 400 by the route layer) with
a human-readable message; sanitisers coerce/trim to safe shapes. Keep this pure
and dependency-free so it is trivially unit-testable.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from services.marketplace.constants import (
    ENGAGEMENT_TYPES, VETTING_TRACKS,
    MAX_NAME_LEN, MAX_HEADLINE_LEN, MAX_BIO_LEN, MAX_LOCATION_LEN,
    MAX_MESSAGE_LEN, MAX_SKILLS, MAX_SKILL_LEN, MAX_SECTORS, MAX_ANSWER_LEN,
)

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_URL_RE = re.compile(r"^https?://[^\s]+$|^[\w.-]+\.[a-z]{2,}(/[^\s]*)?$", re.I)


class ValidationError(ValueError):
    """Raised when a public-form field fails validation."""


def clean_str(value: Any, *, max_len: int, field: str, required: bool = False,
              min_len: int = 0) -> str:
    """Trim to a string, enforce length bounds. Non-strings become ''."""
    if value is None:
        value = ""
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    if required and not value:
        raise ValidationError(f"{field} is required")
    if len(value) < min_len and (value or required):
        raise ValidationError(f"{field} must be at least {min_len} characters")
    if len(value) > max_len:
        raise ValidationError(f"{field} must be at most {max_len} characters")
    return value


def clean_email(value: Any, *, field: str = "email", required: bool = False) -> str:
    v = clean_str(value, max_len=254, field=field, required=required)
    if not v:
        return ""
    if not _EMAIL_RE.match(v):
        raise ValidationError(f"{field} must be a valid email address")
    return v.lower()


def clean_url(value: Any, *, field: str = "url") -> str:
    v = clean_str(value, max_len=300, field=field)
    if not v:
        return ""
    if not _URL_RE.match(v):
        raise ValidationError(f"{field} must be a valid URL")
    return v


def clean_str_list(value: Any, *, max_items: int, max_item_len: int, field: str) -> list[str]:
    """Coerce to a de-duplicated list of trimmed strings within bounds."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        # Accept comma-separated as a convenience.
        value = [p for p in re.split(r"[,\n]", value)]
    if not isinstance(value, (list, tuple)):
        raise ValidationError(f"{field} must be a list")
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        s = (str(item) if item is not None else "").strip()
        if not s:
            continue
        if len(s) > max_item_len:
            s = s[:max_item_len]
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= max_items:
            break
    return out


def clean_track(value: Any, *, default: str = "ml_platform") -> str:
    v = (value or default)
    if v not in VETTING_TRACKS:
        raise ValidationError(f"track must be one of {', '.join(VETTING_TRACKS)}")
    return v


def clean_engagement(value: Any, *, default: str = "both") -> str:
    v = (value or default)
    if v not in ENGAGEMENT_TYPES:
        raise ValidationError(f"engagement must be one of {', '.join(ENGAGEMENT_TYPES)}")
    return v


def clean_int(value: Any, *, field: str, minimum: int = 0, maximum: int = 100,
              default: int = 0) -> int:
    if value in (None, ""):
        return default
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        raise ValidationError(f"{field} must be a number")
    return max(minimum, min(maximum, n))


def clean_money(value: Any, *, field: str) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{field} must be a number")
    if n < 0 or n > 100_000_000:
        raise ValidationError(f"{field} is out of range")
    return round(n, 2)


def validate_leader_payload(data: dict, *, require_name: bool = True) -> dict:
    """Validate + sanitise a leader create/update body → clean kwargs."""
    out: dict[str, Any] = {}
    if require_name or "name" in data:
        out["name"] = clean_str(data.get("name"), max_len=MAX_NAME_LEN,
                                field="name", required=require_name, min_len=2)
    if "headline" in data:
        out["headline"] = clean_str(data.get("headline"), max_len=MAX_HEADLINE_LEN, field="headline")
    if "bio" in data:
        out["bio"] = clean_str(data.get("bio"), max_len=MAX_BIO_LEN, field="bio")
    if "location" in data:
        out["location"] = clean_str(data.get("location"), max_len=MAX_LOCATION_LEN, field="location")
    if "seniority" in data:
        out["seniority"] = clean_str(data.get("seniority"), max_len=60, field="seniority")
    if "comp_expectation" in data:
        out["comp_expectation"] = clean_str(data.get("comp_expectation"), max_len=80,
                                            field="comp_expectation")
    if "skills" in data:
        out["skills"] = clean_str_list(data.get("skills"), max_items=MAX_SKILLS,
                                       max_item_len=MAX_SKILL_LEN, field="skills")
    if "sectors" in data:
        out["sectors"] = clean_str_list(data.get("sectors"), max_items=MAX_SECTORS,
                                        max_item_len=60, field="sectors")
    if "track" in data or require_name:
        out["track"] = clean_track(data.get("track"))
    if "engagement" in data:
        out["engagement"] = clean_engagement(data.get("engagement"))
    if "years_experience" in data:
        out["years_experience"] = clean_int(data.get("years_experience"),
                                             field="years_experience", minimum=0, maximum=60)
    # Contact block (private — revealed only after an accepted intro).
    contact = {}
    if "email" in data:
        contact["email"] = clean_email(data.get("email"), field="email")
    if "phone" in data:
        contact["phone"] = clean_str(data.get("phone"), max_len=40, field="phone")
    if "linkedin" in data:
        contact["linkedin"] = clean_url(data.get("linkedin"), field="linkedin")
    if contact:
        out["contact"] = contact
    return out


def validate_intro_payload(data: dict) -> dict:
    """Validate a request-introduction body."""
    out: dict[str, Any] = {}
    company = data.get("company") or {}
    if not isinstance(company, dict):
        raise ValidationError("company must be an object")
    out["company_name"] = clean_str(
        company.get("name") or data.get("company_name"),
        max_len=MAX_NAME_LEN, field="company name")
    out["message"] = clean_str(data.get("message"), max_len=MAX_MESSAGE_LEN, field="message")
    out["opportunity_id"] = clean_str(data.get("opportunity_id"), max_len=64,
                                      field="opportunity_id")
    out["opportunity_title"] = clean_str(data.get("opportunity_title"), max_len=200,
                                         field="opportunity_title")
    out["first_year_comp"] = clean_money(data.get("first_year_comp"), field="first_year_comp")
    return out


def validate_company_payload(data: dict) -> dict:
    """Validate a company-profile create/update body."""
    out: dict[str, Any] = {}
    out["name"] = clean_str(data.get("name"), max_len=MAX_NAME_LEN, field="company name",
                            required=True, min_len=2)
    out["sector"] = clean_str(data.get("sector"), max_len=80, field="sector")
    out["size"] = clean_str(data.get("size"), max_len=40, field="size")
    out["location"] = clean_str(data.get("location"), max_len=MAX_LOCATION_LEN, field="location")
    out["website"] = clean_url(data.get("website"), field="website")
    out["description"] = clean_str(data.get("description"), max_len=MAX_BIO_LEN, field="description")
    out["contact_name"] = clean_str(data.get("contact_name"), max_len=MAX_NAME_LEN,
                                    field="contact_name")
    out["contact_email"] = clean_email(data.get("contact_email"), field="contact_email")
    return out


def validate_vetting_responses(responses: Any) -> list[dict]:
    """Validate the responses array submitted to the vetting endpoint."""
    if not isinstance(responses, list) or not responses:
        raise ValidationError("responses must be a non-empty array")
    if len(responses) > 20:
        raise ValidationError("too many responses")
    out = []
    for r in responses:
        if not isinstance(r, dict):
            raise ValidationError("each response must be an object")
        text = clean_str(r.get("text") or r.get("response"), max_len=MAX_ANSWER_LEN,
                         field="answer")
        out.append({
            "question_id": clean_str(r.get("question_id") or r.get("id"), max_len=40,
                                     field="question_id"),
            "competency": clean_str(r.get("competency"), max_len=60, field="competency") or "General",
            "weight": r.get("weight"),
            "text": text,
        })
    return out
