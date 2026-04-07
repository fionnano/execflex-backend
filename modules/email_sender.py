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
                     thread_id: str | None = None,
                     plain_body_override: str | None = None) -> bool:
    """
    Send branded intro email and log it.

    If plain_body_override is provided, it replaces the static template
    body entirely — used for LLM-generated outreach emails.
    """

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

    if plain_body_override:
        msg.set_content(plain_body_override)
    else:
        # Plain text fallback (legacy static template)
        plain_body = [
            f"Hi {client_name},",
            "",
            f"As discussed, here's your ExecFlex introduction to {candidate_name}, a {role_text} specialising in {industries_text}.",
            "We believe this could be a valuable conversation for both of you.",
            "",
            f"{candidate_name}, meet {client_name}.",
            "",
            (body_extra or "I'll leave you both to take it forward directly."),
            "",
            "Best regards,",
            "Ai-dan",
            "ExecFlex | Connecting Leaders to Growth",
        ]
        msg.set_content("\n".join(plain_body))

    # Only attach the branded HTML alternative when using the static template.
    # For LLM-generated outreach (plain_body_override set) we send plain-text
    # only — cold outreach performs better without heavy marketing HTML.
    if not plain_body_override:
        html_body = f"""
        <html>
          <body style="font-family: Arial, sans-serif; color: #333;">
            <table style="max-width:600px; margin:auto; border:1px solid #eee; padding:20px;">
              <tr>
                <td>
                  <img src="https://execflex.com/logo.png" alt="ExecFlex Logo" style="width:160px; margin-bottom:20px;" />
                  <p>Hi {client_name},</p>
                  <p>
                    As discussed, here's your <b>ExecFlex introduction</b> to <b>{candidate_name}</b>,
                    a <b>{role_text}</b> specialising in {industries_text}.
                  </p>
                  <p>We believe this could be a valuable conversation for both of you.</p>
                  <p><b>{candidate_name}</b>, meet <b>{client_name}</b>.</p>
                  <p>{body_extra or "I'll leave you both to take it forward directly."}</p>
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


def send_screening_feedback_email(
    candidate_email: str,
    candidate_name: str,
    role_title: str,
    company_name: str,
    overall_score: float,
    scores: list,
    recommendation: str,
) -> bool:
    """
    Send screening feedback to a candidate (EU AI Act Article 86 compliance).
    """
    if not EMAIL_ADDRESS or not EMAIL_PASSWORD:
        print("[Feedback] Email not configured, cannot send feedback")
        return False
    if not _is_valid_email(candidate_email):
        print(f"[Feedback] Invalid email: {candidate_email}")
        return False

    # Build score breakdown
    score_lines_plain = []
    score_rows_html = ""
    rubric = {1: "No relevant evidence", 2: "Limited evidence", 3: "Meets expectations", 4: "Strong evidence", 5: "Exceptional evidence"}
    for s in scores:
        q = s.get("question", "Question")
        sc = s.get("score")
        justification = s.get("score_justification") or s.get("response_summary", "")
        if sc is not None:
            label = rubric.get(sc, f"Score {sc}")
            score_lines_plain.append(f"  - {q}\n    Score: {sc}/5 ({label})\n    Rationale: {justification}")
            score_rows_html += f"""
            <tr>
              <td style="padding:8px; border-bottom:1px solid #eee;">{q}</td>
              <td style="padding:8px; border-bottom:1px solid #eee; text-align:center;"><b>{sc}/5</b><br/><small>{label}</small></td>
              <td style="padding:8px; border-bottom:1px solid #eee; font-size:13px;">{justification}</td>
            </tr>"""
        else:
            score_lines_plain.append(f"  - {q}\n    Not assessed")

    recommendation_label = {
        "strong_proceed": "Strong Proceed",
        "proceed": "Proceed",
        "hold": "Hold",
        "reject": "Not proceeding at this stage",
    }.get(recommendation, recommendation)

    subject = f"Your Screening Feedback — {role_title} at {company_name}"

    msg = EmailMessage()
    msg["From"] = formataddr(("Ainm Search", EMAIL_ADDRESS))
    msg["To"] = candidate_email
    msg["Subject"] = subject

    plain_body = f"""Hi {candidate_name},

Thank you for taking part in the AI screening for the {role_title} role at {company_name}. As required under EU AI Act Article 86, here is your screening feedback.

Overall Score: {overall_score}/5
Outcome: {recommendation_label}

Question-by-Question Breakdown:
{chr(10).join(score_lines_plain)}

How You Were Assessed:
You were assessed on relevance of experience, specific examples given, and outcomes described only. Accent, name, communication style, confidence level, and all personal characteristics were explicitly excluded from scoring. Every candidate for this role was asked the same questions in the same order.

If you have questions about your screening, contact: compliance@ainm.ai

Best regards,
Ainm Search
"""
    msg.set_content(plain_body)

    html_body = f"""
    <html>
      <body style="font-family: Arial, sans-serif; color: #333; max-width: 700px; margin: auto;">
        <div style="padding: 20px; border: 1px solid #eee; border-radius: 8px;">
          <h2 style="color: #2c3e50;">Your Screening Feedback</h2>
          <p>Hi {candidate_name},</p>
          <p>Thank you for taking part in the AI screening for the <b>{role_title}</b> role at <b>{company_name}</b>. As required under EU AI Act Article 86, here is your screening feedback.</p>

          <div style="background: #f8f9fa; padding: 16px; border-radius: 6px; margin: 16px 0;">
            <p style="margin:0;"><b>Overall Score:</b> {overall_score}/5</p>
            <p style="margin:4px 0 0;"><b>Outcome:</b> {recommendation_label}</p>
          </div>

          <h3>Question-by-Question Breakdown</h3>
          <table style="width:100%; border-collapse: collapse;">
            <tr style="background: #f1f3f5;">
              <th style="padding:8px; text-align:left;">Question</th>
              <th style="padding:8px; text-align:center;">Score</th>
              <th style="padding:8px; text-align:left;">Rationale</th>
            </tr>
            {score_rows_html}
          </table>

          <div style="background: #e8f5e9; padding: 12px; border-radius: 6px; margin: 16px 0;">
            <p style="margin:0; font-size: 13px;"><b>How you were assessed:</b> You were assessed on relevance of experience, specific examples given, and outcomes described only. Accent, name, communication style, confidence level, and all personal characteristics were explicitly excluded from scoring. Every candidate for this role was asked the same questions in the same order.</p>
          </div>

          <p style="font-size: 13px; color: #666;">If you have questions about your screening, contact: <a href="mailto:compliance@ainm.ai">compliance@ainm.ai</a></p>

          <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;"/>
          <small style="color:#999;">This feedback was generated in accordance with EU AI Act Article 86 (right to explanation). Ainm Search | {datetime.utcnow().isoformat()}</small>
        </div>
      </body>
    </html>
    """
    msg.add_alternative(html_body, subtype="html")

    try:
        _send_message(msg)
        print(f"[Feedback] Sent screening feedback to {candidate_email} for {role_title}")
        return True
    except Exception as e:
        print(f"[Feedback] Failed to send feedback email: {e}")
        return False


def send_lead_notification(email: str,
                           name: str | None = None,
                           company: str | None = None,
                           message: str | None = None,
                           source: str = "landing_page") -> bool:
    """
    Notify EMAIL_USER that a new inbound lead arrived via /submit-brief.
    """
    if not EMAIL_ADDRESS or not EMAIL_PASSWORD:
        print("[Lead] Email not configured, cannot send lead notification")
        return False

    msg = EmailMessage()
    msg["From"] = formataddr(("Ainm Search Leads", EMAIL_ADDRESS))
    msg["To"] = EMAIL_ADDRESS
    msg["Subject"] = f"New lead: {name or email} ({source})"

    lines = [
        "A new lead just submitted the landing page form.",
        "",
        f"Email:   {email}",
        f"Name:    {name or '(not provided)'}",
        f"Company: {company or '(not provided)'}",
        f"Source:  {source}",
        f"Time:    {datetime.utcnow().isoformat()}Z",
        "",
        "Message:",
        (message or "(none)"),
    ]
    msg.set_content("\n".join(lines))

    try:
        _send_message(msg)
        print(f"[Lead] Notification sent for {email}")
        return True
    except Exception as e:
        print(f"[Lead] Failed to send notification: {e}")
        return False
