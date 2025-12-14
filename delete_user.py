#!/usr/bin/env python3
"""
Delete user by phone number using Supabase Admin API
Cleans up all records created by onboarding service and other user-related data.

Usage: python delete_user.py +447463212071
"""
import os
import sys
import requests
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
    
    # Find user by phone in auth.users (requires admin API)
    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json"
    }
    
    # Search for user by phone in auth.users
    list_url = f"{SUPABASE_URL}/auth/v1/admin/users"
    response = requests.get(list_url, headers=headers, params={"phone": phone})
    
    if response.status_code != 200:
        print(f"Error querying auth users: {response.status_code}")
        print(response.text)
        sys.exit(1)
    
    users = response.json().get("users", [])
    if not users:
        print(f"User with phone {phone} not found in auth.users.")
        sys.exit(0)
    
    user_id = users[0]["id"]
    print(f"Found user ID: {user_id}")
    
    # Delete from related tables (order matters due to foreign keys)
    print("\nDeleting related data...")
    
    # 1. Delete outbound_call_jobs (created by onboarding service)
    try:
        supabase.table("outbound_call_jobs").delete().eq("user_id", user_id).execute()
        print("  ✓ Deleted outbound_call_jobs")
    except Exception as e:
        print(f"  ⚠️  Error deleting outbound_call_jobs: {e}")
    
    # 2. Delete thread participants first (if table exists)
    try:
        supabase.table("thread_participants").delete().eq("user_id", user_id).execute()
        print("  ✓ Deleted thread_participants")
    except Exception as e:
        # Table might not exist or have different structure - that's okay
        pass
    
    # 3. Mark threads as inactive (interactions are append-only, so we can't delete threads)
    try:
        supabase.table("threads").update({"active": False}).or_(f"primary_user_id.eq.{user_id},owner_user_id.eq.{user_id}").execute()
        print("  ✓ Marked threads as inactive")
    except Exception as e:
        print(f"  ⚠️  Error updating threads: {e}")
    
    # Note: Interactions are append-only (event sourcing pattern) and cannot be deleted.
    # They remain as historical records. This is by design.
    print("  ℹ️  Interactions are append-only and remain as historical records")
    
    # 4. Delete organization memberships (if table exists)
    try:
        supabase.table("organization_members").delete().eq("user_id", user_id).execute()
        print("  ✓ Deleted organization_members")
    except Exception as e:
        # Table might not exist - that's okay
        error_msg = str(e)
        if "does not exist" in error_msg:
            pass  # Table doesn't exist, skip silently
        else:
            print(f"  ⚠️  Error deleting organization_members: {e}")
    
    # 5. Delete opportunities created by user
    try:
        supabase.table("opportunities").delete().eq("created_by_user_id", user_id).execute()
        print("  ✓ Deleted opportunities")
    except Exception as e:
        print(f"  ⚠️  Error deleting opportunities: {e}")
    
    # 6. Delete match_suggestions
    try:
        supabase.table("match_suggestions").delete().eq("suggested_user_id", user_id).execute()
        print("  ✓ Deleted match_suggestions")
    except Exception as e:
        print(f"  ⚠️  Error deleting match_suggestions: {e}")
    
    # 7. Delete channel_identities
    try:
        supabase.table("channel_identities").delete().eq("user_id", user_id).execute()
        print("  ✓ Deleted channel_identities")
    except Exception as e:
        print(f"  ⚠️  Error deleting channel_identities: {e}")
    
    # 8. Delete role_assignments (created by onboarding service)
    try:
        supabase.table("role_assignments").delete().eq("user_id", user_id).execute()
        print("  ✓ Deleted role_assignments")
    except Exception as e:
        print(f"  ⚠️  Error deleting role_assignments: {e}")
    
    # 9. Delete user_preferences (created by onboarding service)
    try:
        supabase.table("user_preferences").delete().eq("user_id", user_id).execute()
        print("  ✓ Deleted user_preferences")
    except Exception as e:
        print(f"  ⚠️  Error deleting user_preferences: {e}")
    
    # 10. Delete people_profiles (created by onboarding service)
    try:
        supabase.table("people_profiles").delete().eq("user_id", user_id).execute()
        print("  ✓ Deleted people_profiles")
    except Exception as e:
        print(f"  ⚠️  Error deleting people_profiles: {e}")
    
    # Note: organizations.created_by_user_id has ON DELETE SET NULL, so it will be set to NULL automatically
    # We don't need to delete organizations, just update them
    try:
        supabase.table("organizations").update({"created_by_user_id": None}).eq("created_by_user_id", user_id).execute()
        print("  ✓ Updated organizations (set created_by_user_id to NULL)")
    except Exception as e:
        print(f"  ⚠️  Error updating organizations: {e}")
    
    # Finally, delete auth user (requires admin API)
    # Note: If interactions exist, the auth user deletion may fail due to foreign key constraints.
    # In that case, interactions remain as historical records (by design - event sourcing).
    print("\nDeleting auth user...")
    delete_url = f"{SUPABASE_URL}/auth/v1/admin/users/{user_id}"
    response = requests.delete(delete_url, headers=headers)
    
    if response.status_code == 200:
        print("  ✓ Deleted auth user")
        print(f"\n✅ User {phone} (ID: {user_id}) successfully deleted!")
    elif response.status_code == 404:
        print(f"  ⚠️  Auth user not found (may have been already deleted)")
        print(f"\n✅ Related data cleaned up for user {phone} (ID: {user_id})")
    else:
        error_response = response.json() if response.text else {}
        error_msg = error_response.get("message", response.text)
        
        # Check if the error is due to append-only interactions
        if "append-only" in error_msg.lower() or "interactions" in error_msg.lower():
            print(f"  ⚠️  Cannot delete auth user: interactions are append-only (event sourcing)")
            print(f"     This is expected behavior - interactions remain as historical records.")
            print(f"     To fully remove the user, you may need to:")
            print(f"     1. Manually delete from Supabase Dashboard > Authentication > Users")
            print(f"     2. Or accept that interactions remain as immutable audit trail")
            print(f"\n✅ All deletable data cleaned up for user {phone} (ID: {user_id})")
            print(f"   Note: Interactions and related threads remain as historical records.")
        else:
            print(f"  ⚠️  Could not delete auth user: {response.status_code}")
            print(f"     Response: {error_msg}")
            print(f"     You may need to delete manually from Supabase Dashboard > Authentication > Users")
            print(f"     User ID: {user_id}")
            sys.exit(1)
        
except Exception as e:
    print(f"\n❌ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
