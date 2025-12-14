# Qualification Call System

**Status:** MVP Implementation  
**Date:** 2025-12-16

## Overview

Asynchronous outbound qualification calls triggered after user signup. Uses a job queue pattern with Twilio for voice calls.

## Architecture

1. **Database**: `outbound_call_jobs` table stores queued jobs
2. **Signup Hook**: `POST /voice/enqueue` endpoint (non-blocking)
3. **Worker**: `workers/call_dispatcher.py` processes queued jobs
4. **TwiML**: `/voice/qualification/intro` returns opening message
5. **Webhooks**: `/voice/qualification/status` receives Twilio callbacks

## Components

### Database Table: `outbound_call_jobs`

- Stores job state: queued → running → succeeded/failed
- Links to `threads` and `interactions` for tracking
- Idempotency via `dedupe_key` (prevents duplicate jobs per user per hour)

### Service: `qualification_call_service.py`

- `enqueue_qualification_call()` - Creates job, thread, interaction
- `process_queued_jobs()` - Worker function to dispatch calls

### Routes: `routes/qualification.py`

- `POST /voice/enqueue` - Enqueue job (called after signup)
- `GET/POST /voice/qualification/intro` - TwiML for call opening
- `POST /voice/qualification/status` - Twilio status callback
- `POST /voice/process-jobs` - Manual trigger for job processing

### Worker: `workers/call_dispatcher.py`

- Standalone script to process queued jobs
- Can be run via cron or scheduled task
- Processes up to 10 jobs per run (configurable)

## Usage

### 1. After User Signup

Frontend calls (non-blocking):
```javascript
// After successful Supabase Auth signup
fetch('https://api.execflex.ai/voice/enqueue', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ user_id: user.id })
});
```

Or use Supabase database function:
```sql
SELECT enqueue_qualification_call_for_user('user-uuid');
```

### 2. Process Jobs

**Option A: Cron/Scheduled Task**
```bash
# Every minute
*/1 * * * * cd /path/to/backend && python -m workers.call_dispatcher
```

**Option B: Render Cron Job**
- Create a scheduled task that calls `POST /voice/process-jobs` every minute

**Option C: Manual Trigger**
```bash
curl -X POST https://api.execflex.ai/voice/process-jobs
```

### 3. Monitor Jobs

```sql
-- View queued jobs
SELECT * FROM outbound_call_jobs WHERE status = 'queued';

-- View recent calls
SELECT * FROM outbound_call_jobs ORDER BY created_at DESC LIMIT 10;

-- View associated interactions
SELECT i.*, j.twilio_call_sid, j.status as job_status
FROM interactions i
JOIN outbound_call_jobs j ON j.interaction_id = i.id
WHERE j.status IN ('succeeded', 'failed');
```

## Configuration

### Environment Variables

- `TWILIO_ACCOUNT_SID` - Twilio account SID
- `TWILIO_AUTH_TOKEN` - Twilio auth token
- `TWILIO_PHONE_NUMBER` - Twilio phone number (from)
- `API_BASE_URL` or `VITE_FLASK_API_URL` - Base URL for TwiML/webhooks
- `CALL_DISPATCHER_LIMIT` - Max jobs per run (default: 10)

### Hardcoded Values

- **Destination Phone**: `+447463212071` (as per requirements)

## TwiML Endpoint

Currently uses `<Say>` for text-to-speech:
```xml
<Response>
  <Say voice="alice" language="en-GB">
    Hello, this is ExecFlex. We're calling to welcome you...
  </Say>
  <Hangup/>
</Response>
```

**Future Enhancement**: Replace with `<Play>` using pre-recorded audio from `backend/static/audio/` if available.

## Webhook Flow

1. Twilio initiates call → calls `/voice/qualification/intro`
2. Call progresses → Twilio sends status callbacks to `/voice/qualification/status`
3. Status updates:
   - `initiated` → job status: running
   - `completed` → job status: succeeded, interaction: completed
   - `failed/busy/no-answer` → job status: failed, interaction: failed

## Reused PoC Code

- **Twilio client initialization**: Reused from `config/clients.py`
- **VoiceResponse/TwiML**: Reused from existing `routes/voice.py` pattern
- **Call creation pattern**: Similar to `call_candidate()` in `routes/voice.py`
- **Audio infrastructure**: `backend/static/audio/` directory exists (35+ MP3 files from PoC)

## Testing

### Manual Test

1. Enqueue a test job:
```bash
curl -X POST https://api.execflex.ai/voice/enqueue \
  -H "Content-Type: application/json" \
  -d '{"user_id": null}'
```

2. Process jobs:
```bash
curl -X POST https://api.execflex.ai/voice/process-jobs
```

3. Check job status:
```sql
SELECT * FROM outbound_call_jobs ORDER BY created_at DESC LIMIT 1;
```

### Expected Flow

1. Signup → `POST /voice/enqueue` → Job created with status='queued'
2. Worker runs → Job status='running', Twilio call initiated
3. Call answered → TwiML plays message
4. Call ends → Webhook updates job status='succeeded', interaction completed

## Next Steps (Future)

- [ ] Add pre-recorded audio file for qualification intro
- [ ] Implement `<Gather>` for user response collection
- [ ] Add OpenAI integration for conversation flow
- [ ] Implement transcription handling
- [ ] Add retry logic with exponential backoff
- [ ] Add monitoring/alerting for failed jobs
