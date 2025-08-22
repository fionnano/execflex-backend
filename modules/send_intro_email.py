import smtplib
from email.message import EmailMessage
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

# Get environment variables
EMAIL_ADDRESS = os.getenv("EMAIL_USER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASS")

def send_intro_email(client_name, match_name, recipient_email):
    try:
        # Compose email
        msg = EmailMessage()
        msg["Subject"] = f"ExecFlex Match Intro: {client_name} ↔ {match_name}"
        msg["From"] = EMAIL_ADDRESS
        msg["To"] = recipient_email
        msg.set_content(
            f"Hi,\n\nYou’ve been matched by ExecFlex!\n\n"
            f"{client_name} has been introduced to {match_name}.\n"
            f"We'll follow up shortly to see how it goes.\n\n"
            f"- ExecFlex Team"
        )

        # Send the email using SMTP
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            smtp.send_message(msg)

        print("✅ Email sent successfully!")

        return True
    except Exception as e:
        print("❌ Error sending intro email:", e)
        return False
