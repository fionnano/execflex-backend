# modules/email_sender.py

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# You can move these to env variables for security later
SENDER_EMAIL = "your-email@gmail.com"
SENDER_PASSWORD = "your-app-password"  # Use an app password if using Gmail

def send_intro_email(client_name, candidate_name, recipient_email):
    subject = f"Intro: {client_name} <> {candidate_name}"
    body = (
        f"Hi {client_name},\n\n"
        f"I’d like to introduce you to {candidate_name}, who may be a strong fit for your role.\n"
        f"Let me know if you'd like to continue the conversation.\n\n"
        f"Best,\nAi-dan at ExecFlex"
    )

    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = recipient_email
    msg['Subject'] = subject

    msg.attach(MIMEText(body, 'plain'))

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, recipient_email, msg.as_string())
        server.quit()
        print("✅ Email sent successfully.")
        return True
    except Exception as e:
        print(f"❌ Failed to send email: {e}")
        return False
