"""
Legal-basis gate for unsolicited sourcing, enrichment and outreach
(GDPR-E-004, Art. 6(1)(f) / Art. 14).

People who never applied are pulled from PDL, enriched with personal
contact details and contacted by e-mail and AI phone call. That processing
is only lawful with a documented legitimate-interest assessment (LIA) and
an Art. 14 notice on first contact. Rather than hard-code that the
assessment exists, the pipeline reads it from the environment:

  SOURCING_LEGAL_BASIS  e.g. "legitimate_interest"
  SOURCING_LIA_REF      reference to the signed LIA document, e.g.
                        "iso/LIA-2026-01" or a document URL

Both must be set or ``sourcing_allowed()`` is False and every sourcing,
enrichment and outreach path refuses to run. When allowed, each sourced row
is stamped with the basis and reference, and the Art. 14 notice is appended
to the first outreach e-mail.
"""
import os
from typing import Dict, Optional

NOTICE_VERSION = "art14_v1"


def sourcing_legal_basis() -> Optional[Dict[str, str]]:
    basis = (os.getenv("SOURCING_LEGAL_BASIS") or "").strip()
    lia_ref = (os.getenv("SOURCING_LIA_REF") or "").strip()
    if not basis or not lia_ref:
        return None
    return {"legal_basis": basis, "lia_ref": lia_ref}


def sourcing_allowed() -> bool:
    return sourcing_legal_basis() is not None


def sourcing_block_reason() -> str:
    return (
        "Unsolicited sourcing, enrichment and outreach are disabled until a legal "
        "basis is documented: set SOURCING_LEGAL_BASIS and SOURCING_LIA_REF."
    )


def stamp_legal_basis(source_metadata: Dict, sourced_at: str) -> Dict:
    """Record on a sourced row why we hold the data (Art. 5(2) accountability)."""
    basis = sourcing_legal_basis() or {}
    source_metadata = dict(source_metadata or {})
    source_metadata["legal_basis"] = basis.get("legal_basis")
    source_metadata["lia_ref"] = basis.get("lia_ref")
    source_metadata["sourced_at"] = sourced_at
    return source_metadata


def art14_notice(company_name: Optional[str] = None) -> str:
    """Art. 14 information for people whose data was not collected from them."""
    controller = "ExecFlex / Ainm Search"
    basis = sourcing_legal_basis() or {}
    return (
        "\n\n---\n"
        "How we got your details: your name, role and public profile were obtained "
        "from a professional data provider (People Data Labs), not from you. "
        f"{controller} processes them to tell you about roles like this one"
        + (f" at {company_name}" if company_name else "")
        + f", on the basis of {basis.get('legal_basis') or 'legitimate interest'} "
        f"(assessment ref {basis.get('lia_ref') or 'on file'}). "
        "You can object or ask us to delete your details at any time by replying "
        "'not interested' or e-mailing privacy@execflex.ai, and we will not contact "
        "you again."
    )
