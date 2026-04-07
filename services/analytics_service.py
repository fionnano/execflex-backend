"""
PostHog analytics service.

Every call is wrapped so a PostHog outage, a missing API key, or
the library not being installed never affects the request path.
Production code should always call track(...) directly and not
worry about these edge cases.
"""
import os
import threading
from typing import Optional

_posthog = None
_init_lock = threading.Lock()
_init_attempted = False


def _get_client():
    """
    Lazily import and configure PostHog.

    Returns the posthog module or None if unavailable. Caches the
    result after the first attempt so subsequent calls are cheap.
    """
    global _posthog, _init_attempted
    if _init_attempted:
        return _posthog
    with _init_lock:
        if _init_attempted:
            return _posthog
        _init_attempted = True
        api_key = os.environ.get("POSTHOG_API_KEY")
        if not api_key:
            return None
        try:
            import posthog as ph
            ph.project_api_key = api_key
            ph.host = os.environ.get("POSTHOG_HOST", "https://eu.i.posthog.com")
            # Keep the library quiet on intermittent network failures
            ph.debug = False
            _posthog = ph
            print(f"[Analytics] PostHog initialised (host={ph.host})", flush=True)
        except Exception as e:
            print(f"[Analytics] PostHog init failed: {e}", flush=True)
            _posthog = None
    return _posthog


def track(event_name: str, user_id: Optional[str], properties: Optional[dict] = None) -> None:
    """
    Fire-and-forget analytics event.

    Safe to call with user_id=None for anonymous events (we use the
    string "anonymous" in that case so PostHog can still record them).
    Safe to call before PostHog is configured — returns silently.
    Never raises.
    """
    try:
        ph = _get_client()
        if ph is None:
            return
        distinct_id = user_id or "anonymous"
        ph.capture(
            distinct_id=distinct_id,
            event=event_name,
            properties=properties or {},
        )
    except Exception as e:
        # Analytics failures must never break the request path.
        print(f"[Analytics] track({event_name}) failed: {e}", flush=True)
