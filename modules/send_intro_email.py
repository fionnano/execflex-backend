# modules/send_intro_email.py

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
        print("📨 Preparing to send email...")
        print(f"→ From: {EMAIL_ADDRESS}")
        print(f"→ To: {recipient_email}")
        print(f"→ SMTP: smtp.gmail.com:465")

        if not all([EMAIL_ADDRESS, EMAIL_PASSWORD, SUPABASE_URL, SUPABASE_KEY]):
            print("❌ Missing environment settings.")
            return False

        # Compose email
        msg = EmailMessage()
        msg["Subject"] = f"ExecFlex Intro: {client_name} ↔ {match_name}"
        msg["From"] = EMAIL_ADDRESS
        msg["To"] = recipient_email
        msg.set_content(
            f"Hi,\n\nYou’ve been matched by ExecFlex!\n\n"
            f"{client_name} has been introduced to {match_name}.\n"
            f"We'll follow up shortly to see how it goes.\n\n"
            f"- ExecFlex Team"
        )

        # Send the email
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            smtp.send_message(msg)

        print("✅ Email sent successfully!")

        # Save match to Supabase
        print("💾 Saving match to Supabase...")
        response = supabase.table("executive_matches").insert({
            "user_name": client_name,
            "match_name": match_name,
            "intro_sent": True,
            "recipient_email": recipient_email
        }).execute()

        print("✅ Match saved:", response)

        return True

    except Exception as e:
        print("❌ Error sending intro email:", e)
        return False
