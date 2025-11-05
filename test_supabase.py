from supabase import create_client
from dotenv import load_dotenv
import os

# Load environment variables from .env
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

print("DEBUG URL:", SUPABASE_URL)
print("DEBUG KEY starts with:", SUPABASE_KEY[:10] if SUPABASE_KEY else None)

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Fetch records from your board_opportunities table
response = supabase.table("board_opportunities").select("*").limit(2).execute()

print("Here are your board opportunities:")
print(response.data)
