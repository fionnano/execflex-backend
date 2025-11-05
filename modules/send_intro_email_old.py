import smtplib
import os
from email.message import EmailMessage
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

# Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Email
EMAIL_ADDRESS = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASS")

def send_intro_email(client_name, match_name, recipient_email):
    try:
        print(f"📨 Preparing to send email... (Simulated)")

        # Simulate sending email and saving match
        print(f"→ From: {EMAIL_ADDRESS}")
        print(f"→ To: {recipient_email}")
        print(f"→ Subject: ExecFlex Match Intro: {client_name} ↔ {match_name}")

        # Skip actual email sending for now
        print(f"🧪 Simulated Sending intro: {client_name} ↔ {match_name} to {recipient_email}")

        return True  # Simulate success

    except Exception as e:
        print("❌ Error sending intro email:", e)
        return False
