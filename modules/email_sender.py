# modules/email_sender.py

import os
import smtplib
from email.message import EmailMessage
from email.utils import formataddr
from supabase import create_client
from dotenv import load_dotenv
from datetime import datetime

# Load .env
load_dotenv()

# Supabase setup - lazy initialization
# Try SUPABASE_SERVICE_KEY first (preferred for server-side), fall back to SUPABASE_KEY
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")
supabase = None

def _get_supabase_client():
    """Get or create Supabase client lazily."""
    global supabase
    if supabase is None and SUPABASE_URL and SUPABASE_KEY:
        try:
            supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        except Exception as e:
            print(f"⚠️ Supabase client creation failed: {e}")
            supabase = None
    return supabase

# Email setup
EMAIL_ADDRESS = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASS")
SENDER_NAME = "ExecFlex Introductions"

SMTP_HOST = os.getenv("EMAIL_SMTP_HOST") or "smtp.gmail.com"
SMTP_PORT = int(os.getenv("EMAIL_SMTP_PORT") or "465")


def _send_message(msg: EmailMessage) -> None:
    """Handles SSL (465) and TLS (587)."""
    if SMTP_PORT == 587:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
            smtp.starttls()
            smtp.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as smtp:
            smtp.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            smtp.send_message(msg)


def log_intro(user_type: str,
              requester_name: str,
              requester_email: str,
              requester_company: str | None,
              match_id: str | None,
              status: str = "sent",
              notes: str | None = None,
              thread_id: str | None = None):
    """Log interaction into Supabase interactions table via thread."""
    # Note: This function is kept for backward compatibility but the main logging
    # now happens in routes/introductions.py when creating interactions.
    # If thread_id is provided, we could update the interaction here, but typically
    # the interaction is already created in the route handler.
    client = _get_supabase_client()
    if not client:
        print("⚠️ Supabase not configured; skipping match log.")
        return
    
    # If thread_id is provided, we could update the interaction
    # For now, just log for debugging
    print(f"📝 Intro logged (thread_id: {thread_id}, status: {status})")


def _is_valid_email(email: str) -> bool:
    """Basic email validation check."""
    return email and "@" in email and "." in email


def send_intro_email(client_name: str,
                     client_email: str,
                     candidate_name: str,
                     candidate_email: str,
                     subject: str | None = None,
                     body_extra: str | None = None,
                     candidate_role: str | None = None,
                     candidate_industries: list | None = None,
                     requester_company: str | None = None,
                     user_type: str = "client",
                     match_id: str | None = None,
                     thread_id: str | None = None) -> bool:
    """Send branded intro email and log it."""

    # Validate addresses
    if not _is_valid_email(client_email):
        print(f"❌ Invalid client email: {client_email}")
        return False
    if not _is_valid_email(candidate_email):
        print(f"❌ Invalid candidate email: {candidate_email}")
        return False

    industries_text = ", ".join(candidate_industries) if candidate_industries else "their field"
    role_text = candidate_role or "an executive leader"

    if subject is None:
        subject = f"ExecFlex Intro: {client_name} ↔ {candidate_name} ({role_text}, {industries_text})"

    msg = EmailMessage()
    msg["From"] = formataddr((SENDER_NAME, EMAIL_ADDRESS))
    msg["To"] = client_email
    # CC candidate AND ExecFlex inbox for proof
    msg["Cc"] = f"{candidate_email}, {EMAIL_ADDRESS}"
    msg["Subject"] = subject

    # Plain text fallback
    plain_body = [
        f"Hi {client_name},",
        "",
        f"As discussed, here’s your ExecFlex introduction to {candidate_name}, a {role_text} specialising in {industries_text}.",
        "We believe this could be a valuable conversation for both of you.",
        "",
        f"{candidate_name}, meet {client_name}.",
        "",
        (body_extra or "I’ll leave you both to take it forward directly."),
        "",
        "Best regards,",
        "Ai-dan",
        "ExecFlex | Connecting Leaders to Growth",
    ]
    msg.set_content("\n".join(plain_body))

    # HTML body with simple branding
    html_body = f"""
    <html>
      <body style="font-family: Arial, sans-serif; color: #333;">
        <table style="max-width:600px; margin:auto; border:1px solid #eee; padding:20px;">
          <tr>
            <td>
              <img src="https://execflex.com/logo.png" alt="ExecFlex Logo" style="width:160px; margin-bottom:20px;" />
              <p>Hi {client_name},</p>
              <p>
                As discussed, here’s your <b>ExecFlex introduction</b> to <b>{candidate_name}</b>, 
                a <b>{role_text}</b> specialising in {industries_text}.
              </p>
              <p>We believe this could be a valuable conversation for both of you.</p>
              <p><b>{candidate_name}</b>, meet <b>{client_name}</b>.</p>
              <p>{body_extra or "I’ll leave you both to take it forward directly."}</p>
              <br/>
              <p>Best regards,<br/>
              <b>Ai-dan</b><br/>
              ExecFlex | Connecting Leaders to Growth</p>
              <hr/>
              <small style="color:#999;">This introduction was facilitated via ExecFlex. Timestamp: {datetime.utcnow().isoformat()}</small>
            </td>
          </tr>
        </table>
      </body>
    </html>
    """
    msg.add_alternative(html_body, subtype="html")

    try:
        _send_message(msg)
        print(f"✅ Intro email sent to {client_email} (cc {candidate_email}, {EMAIL_ADDRESS})")

        log_intro(
            user_type=user_type,
            requester_name=client_name,
            requester_email=client_email,
            requester_company=requester_company,
            match_id=match_id,
            status="sent",
            notes=f"Intro made to {candidate_name} ({candidate_email}) for role {role_text} in {industries_text}",
            thread_id=thread_id
        )
        return True

    except Exception as e:
        print(f"❌ Error sending intro email: {e}")

        log_intro(
            user_type=user_type,
            requester_name=client_name,
            requester_email=client_email,
            requester_company=requester_company,
            match_id=match_id,
            status="failed",
            notes=str(e),
            thread_id=thread_id
        )
        return False
