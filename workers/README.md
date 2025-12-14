# Qualification Call Worker

Background worker for processing outbound qualification call jobs.

## Overview

The worker polls the `outbound_call_jobs` table for queued onboarding call jobs and initiates Twilio calls. It runs continuously, processing jobs as they become available.

**Note:** Jobs are automatically created by the database trigger `on_auth_user_created_onboarding` when users sign up. The worker processes these jobs and initiates personalized calls based on signup_mode metadata.

## Running Locally

```bash
cd backend
source venv/bin/activate
python -m workers.call_dispatcher
```

## Running on Render as Background Worker

### Option 1: Background Worker (Recommended)

Render supports **Background Workers** that run continuously:

1. **In Render Dashboard:**
   - Create a new **Background Worker** service
   - Connect to your repository
   - Set:
     - **Build Command:** `pip install -r requirements.txt`
     - **Start Command:** `python -m workers.call_dispatcher`
     - **Environment Variables:** Same as your web service (Supabase, Twilio, etc.)

2. **The worker will:**
   - Run continuously
   - Poll for queued jobs
   - Process them automatically
   - Restart automatically if it crashes

### Option 2: Cron Job (Alternative)

If you prefer scheduled runs instead of continuous polling:

1. **In Render Dashboard:**
   - Create a **Cron Job**
   - Set schedule: `*/1 * * * *` (every minute)
   - **Command:** `python -m workers.call_dispatcher`
   - **Environment Variables:** Same as web service

**Note:** Cron jobs run on a schedule, so there may be a delay between job creation and processing (up to 1 minute with the schedule above).

## Worker Behavior

- **Polling:** Continuously checks for `status='queued'` jobs
- **Processing:** Processes up to 10 jobs per run (configurable via `CALL_DISPATCHER_LIMIT`)
- **Retry Logic:** Failed jobs are retried with exponential backoff (up to 3 attempts)
- **Idempotency:** Dedupe key prevents duplicate jobs for same user within 1 hour

## Environment Variables

Required (same as web service):
- `SUPABASE_URL`
- `SUPABASE_SERVICE_KEY`
- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_PHONE_NUMBER`
- `API_BASE_URL` (for TwiML callback URLs)

Optional:
- `CALL_DISPATCHER_LIMIT` (default: 10) - Max jobs to process per run

## Monitoring

- Check Render logs for worker output
- Monitor `outbound_call_jobs` table for job status
- Check Twilio console for call status
