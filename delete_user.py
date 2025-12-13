#!/usr/bin/env python3
"""
Delete user by phone number using Supabase Admin API
Usage: python delete_user.py +447463212071
"""
import os
import sys
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    print("Error: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in .env")
    sys.exit(1)

# Phone number from command line or default
phone = sys.argv[1] if len(sys.argv) > 1 else "+447463212071"

print(f"Deleting user with phone: {phone}")

try:
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    
    # Find user by phone
    response = supabase.table("users").select("id").eq("phone", phone).limit(1).execute()
    
    if not response.data:
        print(f"User with phone {phone} not found.")
        sys.exit(0)
    
    user_id = response.data[0]["id"]
    print(f"Found user ID: {user_id}")
    
    # Delete from related tables
    print("Deleting related data...")
    
    # Delete role assignments
    supabase.table("role_assignments").delete().eq("user_id", user_id).execute()
    print("  ✓ Deleted role_assignments")
    
    # Delete people_profiles
    supabase.table("people_profiles").delete().eq("user_id", user_id).execute()
    print("  ✓ Deleted people_profiles")
    
    # Delete user_preferences
    supabase.table("user_preferences").delete().eq("user_id", user_id).execute()
    print("  ✓ Deleted user_preferences")
    
    # Delete opportunities
    supabase.table("opportunities").delete().eq("created_by_user_id", user_id).execute()
    print("  ✓ Deleted opportunities")
    
    # Delete channel_identities
    supabase.table("channel_identities").delete().eq("user_id", user_id).execute()
    print("  ✓ Deleted channel_identities")
    
    # Delete threads
    supabase.table("threads").delete().or_(f"primary_user_id.eq.{user_id},owner_user_id.eq.{user_id}").execute()
    print("  ✓ Deleted threads")
    
    # Delete match_suggestions
    supabase.table("match_suggestions").delete().eq("suggested_user_id", user_id).execute()
    print("  ✓ Deleted match_suggestions")
    
    # Delete auth user (requires admin API)
    # Note: Supabase Python client doesn't have direct auth admin methods
    # We need to use the REST API directly
    import requests
    
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json"
    }
    
    # Delete from auth.users via Admin API
    delete_url = f"{SUPABASE_URL}/auth/v1/admin/users/{user_id}"
    response = requests.delete(delete_url, headers=headers)
    
    if response.status_code == 200:
        print("  ✓ Deleted auth user")
        print(f"\n✅ User {phone} successfully deleted!")
    else:
        print(f"  ⚠️  Could not delete auth user: {response.status_code}")
        print(f"     You may need to delete manually from Supabase Dashboard > Authentication > Users")
        print(f"     User ID: {user_id}")
        
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
