"""
Voice conversation helper functions for normalization and validation.
"""


def is_yes(s: str) -> bool:
    """Check if speech input indicates yes/affirmative."""
    return bool(s) and any(w in s.lower() for w in ["yes", "yeah", "yep", "sure", "please", "ok", "okay"])


def normalize_role(text: str | None) -> str | None:
    """Normalize role text to standard format."""
    if not text:
        return None
    t = text.lower()
    if "cfo" in t:
        return "CFO"
    if "ceo" in t:
        return "CEO"
    if "cto" in t:
        return "CTO"
    if "coo" in t:
        return "COO"
    return text.strip().title()


def normalize_industry(text: str | None) -> str | None:
    """Normalize industry text to standard format."""
    if not text:
        return None
    t = text.lower()
    if "fintech" in t or "finance" in t:
        return "Fintech"
    if "insurance" in t:
        return "Insurance"
    if "health" in t:
        return "Healthtech"
    if "saas" in t:
        return "SaaS"
    return text.strip().title()


def normalize_location(text: str | None) -> str | None:
    """Normalize location text to standard format."""
    if not text:
        return None
    t = text.lower()
    if "ireland" in t or "dublin" in t:
        return "Ireland"
    if "uk" in t or "united kingdom" in t or "london" in t:
        return "UK"
    if "remote" in t:
        return "Remote"
    return text.strip().title()


def normalize_availability(text: str | None) -> str | None:
    """Normalize availability text to standard format."""
    if not text:
        return None
    t = text.lower()
    if "fractional" in t or "part" in t or "days" in t:
        return "fractional"
    if "full" in t:
        return "full_time"
    return text.strip().lower()


def is_email_like(text: str | None) -> bool:
    """Check if text looks like an email address."""
    return "@" in (text or "") and "." in (text or "")

