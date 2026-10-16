"""
Redaction helpers for log lines (GDPR-E-003).

Render keeps stdout, so anything printed is retained and searchable by
everyone with dashboard access. Log identifiers, never the contact value:
``mask_email("jane.doe@example.com") == "j***@example.com"`` and
``mask_phone("+353861234567") == "+353***4567"``. Transcript text and system
prompts are never logged; log their lengths instead.
"""
from typing import Any, Optional


def mask_email(value: Any) -> str:
    """Keep the first character of the local part and the domain."""
    if not value or not isinstance(value, str):
        return "<none>"
    local, sep, domain = value.partition("@")
    if not sep:
        return "***"
    return f"{local[:1]}***@{domain}"


def mask_phone(value: Any) -> str:
    """Keep the leading '+' / country hint and the last four digits."""
    if not value or not isinstance(value, str):
        return "<none>"
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) <= 4:
        return "***"
    prefix = "+" if value.strip().startswith("+") else ""
    return f"{prefix}{digits[:3]}***{digits[-4:]}"


def mask_token(value: Optional[str]) -> str:
    """Never log a bearer-style secret; show only that one exists."""
    return "<set>" if value else "<none>"
