# modules/feedback_handler.py

import os
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def save_feedback(user_name, match_name, feedback_text):
    try:
        result = supabase.table("executive_matches").insert({
            "user_name": user_name,
            "match_name": match_name,
            "feedback": feedback_text
        }).execute()

        if result.status_code == 201:
            print("✅ Feedback saved to Supabase")
        else:
            print("⚠️ Something went wrong while saving feedback:", result)
    except Exception as e:
        print("❌ Error saving feedback:", e)
