"""
Results-webhook delivery policy (GDPR-E-005 / SEC-E-016).

Screening and voice-call jobs carry a caller-supplied ``callback_url`` that
receives the full transcript, scores and the candidate portal token. Before
this policy any authenticated caller could point that at any host, unsigned
(a data-export channel and a blind SSRF). Now:

* only hosts on the allow-list receive callbacks, over https;
* only HMAC-signed callbacks are delivered (see
  screening_service._signed_callback_headers), which in turn are only issued
  for service-to-service jobs — so an ordinary account can neither export
  candidate data to its own host nor obtain a genuinely signed payload;
* a callback_url outside the allow-list is rejected when the job is created.

The allow-list is ``CALLBACK_ALLOWED_HOSTS`` (comma-separated host names; a
name also matches its subdomains). Default: ainm.ai.
"""
import os
from typing import List, Optional
from urllib.parse import urlparse

DEFAULT_ALLOWED_HOSTS = ("ainm.ai",)


def allowed_hosts() -> List[str]:
    raw = os.getenv("CALLBACK_ALLOWED_HOSTS")
    if raw is None or not raw.strip():
        return list(DEFAULT_ALLOWED_HOSTS)
    return [h.strip().lower().lstrip(".") for h in raw.split(",") if h.strip()]


def _host_allowed(host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    for allowed in allowed_hosts():
        if host == allowed or host.endswith("." + allowed):
            return True
    return False


def validate_callback_url(url: Optional[str]) -> Optional[str]:
    """Return None when the URL may receive callbacks, else the reason not."""
    if not url or not isinstance(url, str):
        return "callback_url is empty"
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return "callback_url is not a valid URL"
    if parsed.scheme != "https":
        return "callback_url must use https"
    if not parsed.hostname:
        return "callback_url has no host"
    if parsed.username or parsed.password:
        return "callback_url must not embed credentials"
    if not _host_allowed(parsed.hostname):
        return "callback_url host is not on the callback allow-list"
    return None


def callback_url_allowed(url: Optional[str]) -> bool:
    return validate_callback_url(url) is None
