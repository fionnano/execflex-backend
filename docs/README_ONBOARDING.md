# Onboarding Service

**Status:** Production Implementation  
**Date:** 2025-12-17

## Overview

The Onboarding Service initializes application-level state for every new identity created in Supabase Auth. It treats Supabase Auth as an external identity provider and ensures all application records are created automatically.

## Architecture

### Core Principle

**Supabase Auth is identity only.** All onboarding, intent, roles, profiles, and outbound calls are owned by our application layer and database.

### What Happens on Signup

When a new user signs up via Supabase Auth, a database trigger automatically:

1. **Creates `people_profiles` stub** - Minimal profile record for every user
2. **Creates `user_preferences`** - Sets default_mode and last_mode from signup_mode metadata
3. **Creates `role_assignments`** - Assigns initial role (talent/hirer) based on signup_mode
4. **Enqueues outbound onboarding call** - Creates job with signup_mode in artifacts for agent personalization

### Signup Metadata

The frontend passes metadata via Supabase Auth user creation:

```typescript
await supabase.auth.signUp({
  phone: '+1234567890',
  password: 'password',
  options: {
    data: {
      signup_mode: 'talent' | 'hirer',  // Required
      first_name: 'John',                 // Optional
      last_name: 'Doe',                   // Optional
      organization_name: 'Acme Corp'     // Optional (for hirers)
    }
  }
});
```

This metadata is stored in `auth.users.raw_user_meta_data` and read by the database trigger.

## Components

### Database Trigger

**Function:** `initialize_user_onboarding(p_user_id UUID)`  
**Trigger:** `on_auth_user_created_onboarding`  
**Location:** `frontend/supabase/migrations/20251217000000_comprehensive_onboarding_service.sql`

Automatically runs after every `INSERT` into `auth.users`.

### Service: `services/onboarding_service.py`

- `initialize_user_onboarding(user_id)` - Convenience wrapper (usually called by trigger)
- `process_queued_jobs(limit)` - Worker function to dispatch Twilio calls

### Routes: `routes/onboarding.py`

- `POST /voice/enqueue` - Admin-only manual trigger
- `GET/POST /voice/onboarding/intro` - TwiML endpoint (personalized based on signup_mode)
- `POST /voice/onboarding/status` - Twilio status callback
- `POST /voice/process-jobs` - Manual job processing trigger

### Worker: `workers/call_dispatcher.py`

Runs continuously to process queued jobs:

```bash
python -m workers.call_dispatcher --continuous
```

## Agent Personalization

The agent's opening message is personalized based on `signup_mode` from job artifacts:

- **`signup_mode='talent'`**: "help you find executive opportunities"
- **`signup_mode='hirer'`**: "help you find executive talent"
- **Unknown/missing**: asks "are you hiring or looking for work"

This eliminates redundant questions when we already know the user's intent.

## Security

- **Frontend cannot enqueue calls** - No exposed endpoints for normal users
- **Frontend cannot trigger onboarding** - Handled automatically by database trigger
- **Metadata is hints only** - Not used for authorization
- **Admin access** - `/voice/enqueue` requires admin role (JWT + role_assignments check)

## Migration

The system was migrated from "Qualification Call Service" to "Onboarding Service":

- Old: `qualification_call_service.py` → New: `onboarding_service.py`
- Old: `qualification.py` routes → New: `onboarding.py` routes
- Old: `/voice/qualification/*` → New: `/voice/onboarding/*`
- Old: `enqueue_qualification_call_for_user()` → New: `initialize_user_onboarding()`

---

**See also:** `docs/ONBOARDING_SERVICE.md` for detailed documentation
