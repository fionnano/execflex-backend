"""
LinkedIn OAuth and profile import service.
Handles OAuth flow, token management, and profile data import.
"""
import os
import secrets
import hashlib
import base64
import json
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Tuple, Any
from cryptography.fernet import Fernet
from config.clients import supabase_client


# LinkedIn OAuth configuration
LINKEDIN_CLIENT_ID = os.getenv("LINKEDIN_CLIENT_ID")
LINKEDIN_CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET")
LINKEDIN_CALLBACK_URL = os.getenv("LINKEDIN_CALLBACK_URL")
ENCRYPTION_KEY = os.getenv("LINKEDIN_ENCRYPTION_KEY")

# LinkedIn API endpoints
LINKEDIN_AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"

# Required fields for profile completion
REQUIRED_FIELDS = ["first_name", "headline", "location", "industries"]

# OAuth state storage (in-memory for now, should be Redis in production)
_oauth_states: Dict[str, Dict[str, Any]] = {}


def _get_fernet() -> Optional[Fernet]:
    """Get Fernet instance for token encryption."""
    if not ENCRYPTION_KEY:
        print("⚠️ LINKEDIN_ENCRYPTION_KEY not set - tokens will not be properly encrypted")
        return None

    # Ensure the key is properly formatted for Fernet (32 url-safe base64 bytes)
    try:
        # If key is already base64 encoded
        return Fernet(ENCRYPTION_KEY.encode())
    except Exception:
        # Generate a proper key from the provided string
        key = hashlib.sha256(ENCRYPTION_KEY.encode()).digest()
        return Fernet(base64.urlsafe_b64encode(key))


def encrypt_token(token: str) -> str:
    """Encrypt a token for storage."""
    fernet = _get_fernet()
    if fernet:
        return fernet.encrypt(token.encode()).decode()
    # Fallback: base64 encode (not secure, but allows testing)
    return base64.b64encode(token.encode()).decode()


def decrypt_token(encrypted_token: str) -> str:
    """Decrypt a stored token."""
    fernet = _get_fernet()
    if fernet:
        return fernet.decrypt(encrypted_token.encode()).decode()
    # Fallback: base64 decode
    return base64.b64decode(encrypted_token.encode()).decode()


def generate_oauth_state(user_id: str, redirect_after: Optional[str] = None) -> str:
    """
    Generate and store a secure OAuth state parameter.

    Args:
        user_id: The authenticated user's ID
        redirect_after: URL to redirect to after OAuth completion

    Returns:
        State string to use in OAuth URL
    """
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = {
        "user_id": user_id,
        "redirect_after": redirect_after,
        "created_at": datetime.now(timezone.utc).isoformat()
    }

    # Clean up old states (older than 10 minutes)
    cleanup_old_states()

    return state


def validate_oauth_state(state: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Validate and consume an OAuth state parameter.

    Args:
        state: State string from OAuth callback

    Returns:
        Tuple of (user_id, redirect_after, error_message)
    """
    if state not in _oauth_states:
        return None, None, "Invalid or expired OAuth state"

    state_data = _oauth_states.pop(state)

    # Check if state is too old (10 minutes max)
    created_at = datetime.fromisoformat(state_data["created_at"])
    if datetime.now(timezone.utc) - created_at > timedelta(minutes=10):
        return None, None, "OAuth state has expired"

    return state_data["user_id"], state_data.get("redirect_after"), None


def cleanup_old_states():
    """Remove OAuth states older than 10 minutes."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
    expired_states = [
        state for state, data in _oauth_states.items()
        if datetime.fromisoformat(data["created_at"]) < cutoff
    ]
    for state in expired_states:
        _oauth_states.pop(state, None)


def get_oauth_url(user_id: str, redirect_after: Optional[str] = None) -> Dict[str, str]:
    """
    Generate LinkedIn OAuth authorization URL.

    Args:
        user_id: The authenticated user's ID
        redirect_after: URL to redirect to after OAuth completion

    Returns:
        Dict with 'url' and 'state' keys
    """
    if not LINKEDIN_CLIENT_ID or not LINKEDIN_CALLBACK_URL:
        raise ValueError("LinkedIn OAuth not configured. Set LINKEDIN_CLIENT_ID and LINKEDIN_CALLBACK_URL.")

    state = generate_oauth_state(user_id, redirect_after)

    # LinkedIn OAuth scopes
    # openid: required for userinfo endpoint
    # profile: basic profile info (name, picture)
    # email: email address
    # r_basicprofile: access to profile URL and more details
    scopes = "openid profile email r_basicprofile"

    params = {
        "response_type": "code",
        "client_id": LINKEDIN_CLIENT_ID,
        "redirect_uri": LINKEDIN_CALLBACK_URL,
        "state": state,
        "scope": scopes
    }

    url = f"{LINKEDIN_AUTH_URL}?" + "&".join(f"{k}={v}" for k, v in params.items())

    return {
        "url": url,
        "state": state
    }


def exchange_code_for_tokens(code: str) -> Dict[str, Any]:
    """
    Exchange OAuth authorization code for access tokens.

    Args:
        code: Authorization code from OAuth callback

    Returns:
        Dict with access_token, expires_in, and optionally refresh_token
    """
    if not LINKEDIN_CLIENT_ID or not LINKEDIN_CLIENT_SECRET or not LINKEDIN_CALLBACK_URL:
        raise ValueError("LinkedIn OAuth not configured")

    response = requests.post(
        LINKEDIN_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": LINKEDIN_CALLBACK_URL,
            "client_id": LINKEDIN_CLIENT_ID,
            "client_secret": LINKEDIN_CLIENT_SECRET
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )

    if response.status_code != 200:
        error_data = response.json() if response.text else {}
        error_msg = error_data.get("error_description", error_data.get("error", "Token exchange failed"))
        raise Exception(f"LinkedIn token exchange failed: {error_msg}")

    return response.json()


def fetch_linkedin_profile(access_token: str) -> Dict[str, Any]:
    """
    Fetch user profile data from LinkedIn API.

    Args:
        access_token: Valid LinkedIn access token

    Returns:
        Dict with profile data
    """
    # First, get basic userinfo
    response = requests.get(
        LINKEDIN_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"}
    )

    if response.status_code != 200:
        raise Exception(f"Failed to fetch LinkedIn profile: {response.status_code}")

    userinfo = response.json()

    # Try to get additional data from /v2/me endpoint
    try:
        print("🔍 Attempting to fetch /v2/me endpoint...")
        me_response = requests.get(
            "https://api.linkedin.com/v2/me",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        print(f"🔍 /v2/me response status: {me_response.status_code}")
        if me_response.status_code == 200:
            me_data = me_response.json()
            print(f"📥 LinkedIn /v2/me data: {json.dumps(me_data, indent=2)}")
            # Merge any useful fields
            userinfo["_me_data"] = me_data
        else:
            print(f"⚠️ /v2/me returned {me_response.status_code}: {me_response.text[:500]}")
    except Exception as e:
        print(f"⚠️ Could not fetch /v2/me: {e}")

    return userinfo


def map_linkedin_to_profile(linkedin_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Map LinkedIn profile data to people_profiles schema.

    Args:
        linkedin_data: Raw data from LinkedIn userinfo endpoint

    Returns:
        Dict with mapped field values
    """
    # Log raw LinkedIn data for debugging
    print(f"📥 Raw LinkedIn userinfo data: {json.dumps(linkedin_data, indent=2)}")

    # LinkedIn userinfo endpoint returns:
    # - sub: LinkedIn member ID
    # - name: Full name
    # - given_name: First name
    # - family_name: Last name
    # - picture: Profile picture URL
    # - email: Email address
    # - locale: Language/country info

    mapped = {}

    # Map available fields
    if linkedin_data.get("given_name"):
        mapped["first_name"] = linkedin_data["given_name"]

    if linkedin_data.get("family_name"):
        mapped["last_name"] = linkedin_data["family_name"]

    if linkedin_data.get("picture"):
        mapped["headshot_url"] = linkedin_data["picture"]
        print(f"✅ Mapped headshot_url: {linkedin_data['picture']}")

    # LinkedIn member ID (internal identifier - NOT usable for public profile URL)
    if linkedin_data.get("sub"):
        mapped["linkedin_member_id"] = linkedin_data["sub"]
        # Note: LinkedIn's sub claim is an internal ID that does NOT work as a public URL
        # The user must manually provide their vanity URL (e.g., linkedin.com/in/johndoe)
        print(f"✅ Mapped linkedin_member_id: {linkedin_data['sub']}")

    # Note: LinkedIn's basic scopes don't provide headline, location, skills, or industries
    # Those would require additional API calls with different scopes
    # For MVP, we get what's available and ask for the rest in the completion form

    print(f"📤 Mapped profile data: {mapped}")
    return mapped


def store_connection(
    user_id: str,
    access_token: str,
    expires_in: int,
    refresh_token: Optional[str] = None,
    scopes: Optional[List[str]] = None
) -> None:
    """
    Store or update LinkedIn connection data.

    Args:
        user_id: User's ID
        access_token: LinkedIn access token
        expires_in: Token validity in seconds
        refresh_token: Optional refresh token
        scopes: List of granted scopes
    """
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=expires_in)

    data = {
        "user_id": user_id,
        "provider": "linkedin",
        "status": "active",
        "scopes": scopes or ["openid", "profile", "email"],
        "access_token_encrypted": encrypt_token(access_token),
        "expires_at": expires_at.isoformat(),
        "linked_at": now.isoformat(),
        "last_sync_at": now.isoformat(),
        "updated_at": now.isoformat()
    }

    if refresh_token:
        data["refresh_token_encrypted"] = encrypt_token(refresh_token)

    # Upsert connection
    try:
        existing = supabase_client.table("linkedin_connections")\
            .select("user_id")\
            .eq("user_id", user_id)\
            .limit(1)\
            .execute()

        if existing.data:
            # Update existing
            supabase_client.table("linkedin_connections")\
                .update(data)\
                .eq("user_id", user_id)\
                .execute()
        else:
            # Insert new
            data["created_at"] = now.isoformat()
            supabase_client.table("linkedin_connections")\
                .insert(data)\
                .execute()

        print(f"✅ Stored LinkedIn connection for user {user_id}")
    except Exception as e:
        print(f"❌ Failed to store LinkedIn connection: {e}")
        raise


def import_profile_data(user_id: str, linkedin_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Import LinkedIn data into people_profiles.
    Uses fill-empty-first strategy - only updates fields that are currently null/empty.

    Args:
        user_id: User's ID
        linkedin_data: Data from LinkedIn API

    Returns:
        Dict with imported_fields and missing_fields
    """
    # Map LinkedIn data to our schema
    mapped_data = map_linkedin_to_profile(linkedin_data)

    # Get current profile
    profile_result = supabase_client.table("people_profiles")\
        .select("*")\
        .eq("user_id", user_id)\
        .limit(1)\
        .execute()

    current_profile = profile_result.data[0] if profile_result.data else {}
    profile_exists = bool(profile_result.data)

    # Determine which fields to update (fill-empty-first strategy)
    fields_to_update = {}
    imported_fields = []

    for field, value in mapped_data.items():
        if value:
            current_value = current_profile.get(field)
            # Update if current is empty/null
            if not current_value:
                fields_to_update[field] = value
                imported_fields.append(field)

    # Always set LinkedIn connection metadata
    now = datetime.now(timezone.utc).isoformat()
    fields_to_update["linkedin_connected_at"] = now

    # Determine profile source
    if not profile_exists or current_profile.get("profile_source") == "manual":
        if imported_fields:
            fields_to_update["profile_source"] = "linkedin"
    elif current_profile.get("profile_source") == "linkedin":
        pass  # Keep as linkedin
    else:
        fields_to_update["profile_source"] = "mixed"

    # Update or create profile
    if profile_exists:
        fields_to_update["updated_at"] = now
        supabase_client.table("people_profiles")\
            .update(fields_to_update)\
            .eq("user_id", user_id)\
            .execute()
    else:
        fields_to_update["user_id"] = user_id
        fields_to_update["created_at"] = now
        fields_to_update["updated_at"] = now
        supabase_client.table("people_profiles")\
            .insert(fields_to_update)\
            .execute()

    # Calculate missing required fields
    updated_profile = {**current_profile, **fields_to_update}
    missing_fields = []

    for field in REQUIRED_FIELDS:
        value = updated_profile.get(field)
        if not value or (isinstance(value, list) and len(value) == 0):
            missing_fields.append(field)

    print(f"✅ Imported LinkedIn data for user {user_id}: {imported_fields}")
    print(f"   Missing fields: {missing_fields}")

    return {
        "imported_fields": imported_fields,
        "missing_fields": missing_fields,
        "profile_source": fields_to_update.get("profile_source", current_profile.get("profile_source", "manual"))
    }


def get_connection_status(user_id: str) -> Dict[str, Any]:
    """
    Get LinkedIn connection status and profile completion for a user.

    Args:
        user_id: User's ID

    Returns:
        Dict with connected, imported_fields, missing_fields, completion_score
    """
    # Check if connected
    connection_result = supabase_client.table("linkedin_connections")\
        .select("status, linked_at, last_sync_at")\
        .eq("user_id", user_id)\
        .eq("status", "active")\
        .limit(1)\
        .execute()

    connected = bool(connection_result.data)

    # Get profile data
    profile_result = supabase_client.table("people_profiles")\
        .select("*")\
        .eq("user_id", user_id)\
        .limit(1)\
        .execute()

    profile = profile_result.data[0] if profile_result.data else {}

    # Calculate imported and missing fields
    imported_fields = []
    missing_fields = []

    # Track which fields have data
    field_checks = {
        "first_name": profile.get("first_name"),
        "last_name": profile.get("last_name"),
        "headline": profile.get("headline"),
        "bio": profile.get("bio"),
        "location": profile.get("location"),
        "skills": profile.get("skills"),
        "industries": profile.get("industries"),
        "headshot_url": profile.get("headshot_url")
    }

    for field, value in field_checks.items():
        if value and (not isinstance(value, list) or len(value) > 0):
            imported_fields.append(field)
        elif field in REQUIRED_FIELDS or field == "skills":
            missing_fields.append(field)

    # Calculate completion score (0-100)
    total_fields = len(field_checks)
    filled_fields = len(imported_fields)
    completion_score = int((filled_fields / total_fields) * 100) if total_fields > 0 else 0

    return {
        "connected": connected,
        "imported_fields": imported_fields,
        "missing_fields": missing_fields,
        "completion_score": completion_score,
        "linked_at": connection_result.data[0].get("linked_at") if connected else None,
        "last_sync_at": connection_result.data[0].get("last_sync_at") if connected else None
    }


def record_skip_event(user_id: str) -> None:
    """
    Record that user skipped LinkedIn connection.
    This is stored in profile metadata for analytics.

    Args:
        user_id: User's ID
    """
    now = datetime.now(timezone.utc).isoformat()

    # Get or create profile
    profile_result = supabase_client.table("people_profiles")\
        .select("profile_completion")\
        .eq("user_id", user_id)\
        .limit(1)\
        .execute()

    if profile_result.data:
        current_completion = profile_result.data[0].get("profile_completion") or {}
        current_completion["linkedin_skipped_at"] = now
        current_completion["linkedin_skip_count"] = current_completion.get("linkedin_skip_count", 0) + 1

        supabase_client.table("people_profiles")\
            .update({
                "profile_completion": current_completion,
                "updated_at": now
            })\
            .eq("user_id", user_id)\
            .execute()
    else:
        # Create profile with skip event
        supabase_client.table("people_profiles")\
            .insert({
                "user_id": user_id,
                "profile_completion": {
                    "linkedin_skipped_at": now,
                    "linkedin_skip_count": 1
                },
                "profile_source": "manual",
                "created_at": now,
                "updated_at": now
            })\
            .execute()

    print(f"📝 Recorded LinkedIn skip for user {user_id}")


def handle_oauth_callback(code: str, state: str) -> Dict[str, Any]:
    """
    Complete OAuth flow: validate state, exchange code, fetch profile, import data.

    Args:
        code: Authorization code from LinkedIn
        state: State parameter for validation

    Returns:
        Dict with success, user_id, redirect_after, imported_fields, missing_fields
    """
    # Validate state
    user_id, redirect_after, error = validate_oauth_state(state)
    if error:
        return {"success": False, "error": error}

    try:
        # Exchange code for tokens
        token_data = exchange_code_for_tokens(code)
        access_token = token_data.get("access_token")
        expires_in = token_data.get("expires_in", 3600)
        refresh_token = token_data.get("refresh_token")

        # Store connection
        store_connection(
            user_id=user_id,
            access_token=access_token,
            expires_in=expires_in,
            refresh_token=refresh_token
        )

        # Fetch and import profile
        linkedin_data = fetch_linkedin_profile(access_token)
        import_result = import_profile_data(user_id, linkedin_data)

        return {
            "success": True,
            "user_id": user_id,
            "redirect_after": redirect_after,
            "imported_fields": import_result["imported_fields"],
            "missing_fields": import_result["missing_fields"]
        }

    except Exception as e:
        print(f"❌ LinkedIn OAuth callback error: {e}")
        return {
            "success": False,
            "error": str(e),
            "user_id": user_id,
            "redirect_after": redirect_after
        }
