import smtplib
from email.message import EmailMessage
import os
from dotenv import load_dotenv
from supabase import create_client

# Load environment variables
load_dotenv()

# Email configuration
EMAIL_HOST = os.getenv("EMAIL_HOST")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", 587))
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")

# Supabase configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")  # ✅ Service role key
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def send_intro_email(client_name, match_name, recipient_email):
    if not all([EMAIL_HOST, EMAIL_PORT, EMAIL_USER, EMAIL_PASS]):
        print("❌ Missing email environment settings.")
        return False

    msg = EmailMessage()
    msg["Subject"] = f"ExecFlex Introduction: {client_name} ↔ {match_name}"
    msg["From"] = EMAIL_USER
    msg["To"] = recipient_email

    msg.set_content(f"""
    Hi {recipient_email},

    We’re delighted to introduce you to {match_name} on behalf of {client_name} via ExecFlex.

    Please feel free to connect directly to explore the opportunity further.

    Best regards,  
    ExecFlex Team
    """)

    try:
        with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT) as server:
            server.starttls()
            server.login(EMAIL_USER, EMAIL_PASS)
            server.send_message(msg)
            print("✅ Intro email sent!")
            return True
    except Exception as e:
        print(f"❌ Failed to send email: {e}")
        return False

def log_match_history(user_name, match_title, match_company):
    try:
        data = {
            "user_name": user_name,
            "match_title": match_title,
            "match_company": match_company
        }
        response = supabase.table("match_history").insert(data).execute()
        print("✅ Match history logged.")
    except Exception as e:
        print(f"❌ Failed to log match history: {e}")
