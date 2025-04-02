# modules/main.py

import time
from modules.listen import listen
from modules.respond import generate_response
from modules.speak import speak
from modules.match_finder import find_best_match
from modules.email_sender import send_intro_email
from modules.feedback_handler import save_feedback
import os

def run_intro():
    intro_text = (
        "Hi, I’m Ai-dan, your advisor at ExecFlex. "
        "We connect ambitious companies to the leaders who turn vision into uncapped growth—and vice versa. "
        "To start, can you tell me your name and whether you're looking to hire or be hired?"
    )
    speak(intro_text)

def main():
    print("🟢 ExecFlex Voice Agent (Ai-dan) is running. Speak now...")
    run_intro()

    state = {
        "name": "there",
        "type": None,       # "client" or "candidate"
        "role": None,
        "industry": None,
        "culture": None,
        "match": None,
        "match_suggested": False
    }

    while True:
        try:
            print("Listening...")
            user_input = listen()
            if not user_input:
                print("No speech detected.")
                continue

            print(f"You said: {user_input}")

            # Extract name
            if "my name is" in user_input.lower():
                state["name"] = user_input.split("my name is")[-1].strip().split(" ")[0].capitalize()

            # Determine type
            if "hire" in user_input.lower():
                state["type"] = "client"
            elif "looking for a role" in user_input.lower() or "looking to work" in user_input.lower():
                state["type"] = "candidate"

            if "cto" in user_input.lower():
                state["role"] = "CTO"
            if "fintech" in user_input.lower():
                state["industry"] = "Fintech"
            if any(word in user_input.lower() for word in ["culture", "collaborative", "fast-paced", "team"]):
                state["culture"] = "Startup Culture"

            # What info is missing?
            missing = []
            if not state["type"]:
                missing.append("whether you're hiring or looking for a role")
            if not state["role"]:
                missing.append("the role you're focused on")
            if not state["industry"]:
                missing.append("the industry")
            if not state["culture"]:
                missing.append("your company or team culture")

            if not missing and not state["match_suggested"]:
                match = find_best_match(
                    match_type=state["type"],
                    role=state["role"],
                    industry=state["industry"],
                    culture=state["culture"]
                )
                state["match"] = match
                state["match_suggested"] = True

                if match:
                    speak(
                        f"Thanks, {state['name']}. Based on what you’ve told me, I’d recommend {match['name']}. "
                        f"{match['summary']}. Would you like an email introduction?"
                    )
                    confirmation = listen()
                    if confirmation and "yes" in confirmation.lower():
                        recipient_email = "recipient@example.com"  # Replace with your test address
                        speak("Great! I’ll send that intro now.")
                        send_intro_email(state["name"], match["name"], recipient_email)

                        # Ask for feedback
                        speak("Just before we wrap up, how do you feel about this match?")
                        feedback = listen()
                        if feedback:
                            save_feedback(state["name"], match["name"], feedback)

                        speak("Thanks! You're all set for now.")
                        break
                    else:
                        speak("No problem! Let me know if you'd like to explore other profiles.")
                        break
                else:
                    speak("Thanks for sharing everything. I don’t have a perfect match yet, but I’ll follow up soon.")
                    break

            elif missing:
                speak(f"Could you tell me more about {missing[0]}?")
                continue

        except KeyboardInterrupt:
            print("\n🛑 Stopping Ai-dan. Goodbye!")
            break

if __name__ == "__main__":
    main()
