# main.py

import os
from dotenv import load_dotenv
from modules.listen import listen
from modules.speak import speak
from modules.match_finder import find_best_match
from modules.email_sender import send_intro_email
from supabase import create_client

# Load env
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def run_intro():
    speak(
        "Hi, I’m Ai-dan, your advisor at ExecFlex. "
        "We connect ambitious companies to the leaders who turn vision into uncapped growth—and vice versa. "
        "Let’s find your perfect match. "
        "Just tell me in one go—are you hiring or looking, and what type of role or leader are you focused on?"
    )


def main():
    print("🟢 ExecFlex Voice Agent (Ai-dan) is running...")
    run_intro()

    try:
        print("Listening...")
        user_input = listen()
        print(f"You said: {user_input}")

        # Defaults
        name = "there"
        role = "Director"
        industry = "General"
        match_type = "candidate" if "hire" in user_input.lower() else "client"

        if "cto" in user_input.lower():
            role = "CTO"
        elif "cfo" in user_input.lower():
            role = "CFO"
        elif "ceo" in user_input.lower():
            role = "CEO"

        if "fintech" in user_input.lower():
            industry = "Fintech"
        elif "insurance" in user_input.lower():
            industry = "Insurance"

        matches = find_best_match(
            industry=industry,
            expertise=role,
            availability="fractional",
            min_experience=5,
            max_salary=200000,
            location="Ireland"
        )

        if matches and isinstance(matches, list) and len(matches) > 0:
            match = matches[0]  # take best for now

            speak(
                f"Based on what you shared, I recommend {match.get('name', 'an executive')} "
                f"for the role of {match.get('role', 'a leadership position')} "
                f"in {match.get('location', 'unknown location')}. "
                f"{match.get('summary','')[:150]}... Would you like me to make an email introduction?"
            )

            confirmation = listen()
            if confirmation and "yes" in confirmation.lower():
                speak("Great! What’s the best email address for the introduction?")
                client_email = listen()
                if not client_email:
                    client_email = "fallback@example.com"

                candidate_email = match.get("email") or "candidate@example.com"

                speak("Sending the introduction now.")
                ok = send_intro_email(
    client_name=name,
    client_email=client_email,
    candidate_name=match.get("name", "an executive"),
    candidate_email=candidate_email,
    subject=None,  # auto-builds with role + industries
    body_extra=f"Context: role {match.get('role','')} in {match.get('location','')}.",
    candidate_role=match.get("role"),
    candidate_industries=match.get("industries", []),
    requester_company=None,
    user_type=match_type,
    match_id=match.get("id")
)
   

                if ok:
                    speak("Done — I’ve emailed the introduction and logged it in Supabase.")
                else:
                    speak("I tried to send the email but hit an error. I’ll follow up shortly.")
            else:
                speak("No problem. You can always ask me for more profiles later.")
        else:
            speak("Thanks for sharing. I don’t have a perfect match right now, but I’ll follow up soon.")

    except KeyboardInterrupt:
        print("\n🛑 Stopping Ai-dan. Goodbye!")


if __name__ == "__main__":
    main()
