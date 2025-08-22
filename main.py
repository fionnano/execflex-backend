# modules/main.py

import time
from modules.listen import listen
from modules.respond import generate_response
from modules.speak import speak
from modules.match_finder import find_best_match
from modules.email_sender import send_intro_email, log_match_history
from modules.feedback_handler import save_feedback
import os

def run_intro():
    speak(
        "Hi, I’m Ai-dan, your advisor at ExecFlex. "
        "We connect ambitious companies to the leaders who turn vision into uncapped growth—and vice versa. "
        "Let’s find your perfect match. Just tell me in one go—are you hiring or looking, and what type of role or leader are you focused on?"
    )

def main():
    print("🟢 ExecFlex Voice Agent (Ai-dan) is running...")
    run_intro()

    try:
        print("Listening...")
        user_input = listen()

        print(f"You said: {user_input}")

        # Basic natural language parsing
        name = "there"
        role = "Director"
        industry = "Technology"
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
            availability="part_time",
            min_experience=5,
            max_salary=200000,
            location="London"
        )

        if matches:
            match = matches[0]
            speak(
                f"Thanks. Based on what you told me, I’d recommend {match['title']} at {match['company_info']['name']} in {match['location']}. "
                f"{match['description'][:200]}... Would you like an email introduction?"
            )

            confirmation = listen()
            if confirmation and "yes" in confirmation.lower():
                recipient_email = "recipient@example.com"  # Replace with real one later
                speak("Great! Sending the intro now.")
                send_intro_email(name, match['title'], recipient_email)

                # ✅ Log match history to Supabase
                try:
                    log_match_history(name, match['title'], match['company_info']['name'])
                except Exception as e:
                    print(f"❌ Could not log match history: {e}")

                speak("Before we finish—how do you feel about this match?")
                feedback = listen()
                if feedback:
                    save_feedback(name, match['title'], feedback)
                speak("Thanks! You're all set.")
            else:
                speak("No problem! You can always ask me for more profiles later.")
        else:
            speak("Thanks for sharing. I don’t have a perfect match just now, but I’ll follow up soon.")

    except KeyboardInterrupt:
        print("\n🛑 Stopping Ai-dan. Goodbye!")

if __name__ == "__main__":
    main()
