from modules.email_sender import send_intro_email

# Change these two to real addresses you can see
CLIENT_NAME = "Fionnan"
CLIENT_EMAIL = "fionnano@gmail.com"         # <- your inbox to receive 'To:'
CANDIDATE_NAME = "Sarah O’Neill (Test)"
CANDIDATE_EMAIL = "fionnano@gmail.com"   # <- a second inbox you can check, or same as above

ok = send_intro_email(
    client_name=CLIENT_NAME,
    client_email=CLIENT_EMAIL,
    candidate_name=CANDIDATE_NAME,
    candidate_email=CANDIDATE_EMAIL,
    subject="(TEST) Introduction via ExecFlex: Fionnan ↔ Sarah O’Neill",
    body_extra="Context: test email from ExecFlex backend."
)

print("Result:", ok)
