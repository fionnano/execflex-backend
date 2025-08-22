from supabase import create_client
from dotenv import load_dotenv
import os

# Load Supabase keys from .env
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def save_feedback(user_name, match_name, feedback_text):
    try:
        data = {
            "user_name": user_name,
            "match_name": match_name,
            "feedback_text": feedback_text,
        }
        response = supabase.table("feedback").insert(data).execute()
        print("✅ Feedback saved to Supabase.")
    except Exception as e:
        print(f"❌ Failed to save feedback: {e}")
