# modules/match_finder.py

import os
from supabase import create_client
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def find_best_match(match_type, role, industry, culture):
    table = "executive_profiles" if match_type == "client" else "hiring_requirements"

    try:
        response = (
            supabase.table(table)
            .select("*")
            .ilike("role", f"%{role}%")
            .ilike("industry", f"%{industry}%")
            .ilike("culture", f"%{culture}%")
            .limit(1)
            .execute()
        )

        data = response.data

        if data and len(data) > 0:
            match = data[0]
            return {
                "name": match.get("name", "Unnamed"),
                "summary": match.get("summary", "No summary provided."),
                "email": match.get("email", "noemail@example.com"),
                "id": match.get("id")
            }
        else:
            return None

    except Exception as e:
        print("❌ Error in find_best_match:", e)
        return None
