"""
Environment checks shared by authentication and webhook verification.

Production is signalled by APP_ENV or FLASK_ENV set to "production" or "prod".
An unset variable is not production. Callers must still fail closed: a missing
key refuses the request, it does not skip the check.
"""
import os


def is_production() -> bool:
    for name in ("APP_ENV", "FLASK_ENV"):
        value = (os.getenv(name) or "").strip().lower()
        if value in ("production", "prod"):
            return True
    return False


def unsigned_twilio_webhooks_allowed() -> bool:
    """
    Local-only escape hatch for machines that cannot present a Twilio signature.

    Set TWILIO_ALLOW_UNSIGNED_WEBHOOKS=1 together with a non-production
    APP_ENV. The flag is ignored when APP_ENV or FLASK_ENV is production/prod.
    """
    flag = (os.getenv("TWILIO_ALLOW_UNSIGNED_WEBHOOKS") or "").strip().lower()
    if flag not in ("1", "true", "yes"):
        return False
    if is_production():
        print(
            "SECURITY: TWILIO_ALLOW_UNSIGNED_WEBHOOKS is set in production and will be ignored",
            flush=True,
        )
        return False
    return True
