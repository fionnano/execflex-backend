# ExecFlex Backend

Modular Flask API server for ExecFlex platform.

## Structure

```
backend/
├── server.py                 # Main entry point
├── config/                   # Configuration and clients
│   ├── app_config.py        # Environment variables & config
│   └── clients.py           # External service clients (Supabase, Twilio, OpenAI)
├── services/                # Business logic services
│   ├── tts_service.py       # Text-to-speech generation & caching
│   ├── gpt_service.py       # GPT conversation rephrasing
│   ├── voice_session_service.py  # Voice call session management
│   └── voice_conversation_service.py  # Voice conversation flow logic
├── utils/                   # Utility functions
│   ├── response_helpers.py  # Flask response helpers (ok, bad)
│   └── voice_helpers.py     # Voice normalization helpers
├── routes/                  # Route handlers (Flask blueprints)
│   ├── health.py           # Health check endpoints
│   ├── matching.py         # Executive matching endpoints
│   ├── roles.py            # Role posting endpoints
│   ├── introductions.py    # Introduction request endpoints
│   ├── feedback.py         # Feedback submission endpoint
│   └── voice.py            # Voice/telephony endpoints
├── modules/                 # Existing business logic modules
│   ├── match_finder.py
│   └── email_sender.py
└── static/                  # Static files (TTS audio cache)
```

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables (see .env.example)
export SUPABASE_URL=...
export SUPABASE_SERVICE_KEY=...

# Run server
python server.py
```

## Configuration

Required environment variables are defined in `config/app_config.py`.

### Required
- `SUPABASE_URL` - Supabase project URL
- `SUPABASE_SERVICE_KEY` - Supabase service role key
- `EMAIL_USER` - Gmail address for sending emails
- `EMAIL_PASS` - Gmail password/app password
- `PORT` - Server port (default: 5001)

### Optional (Voice Features)
- `TWILIO_ACCOUNT_SID` - Twilio account SID
- `TWILIO_AUTH_TOKEN` - Twilio auth token
- `TWILIO_PHONE_NUMBER` - Twilio phone number
- `ELEVEN_API_KEY` - ElevenLabs API key for TTS
- `ELEVEN_VOICE_ID` - ElevenLabs voice ID
- `OPENAI_API_KEY` - OpenAI API key for conversation rephrasing

## API Endpoints

### Health
- `GET /` - Root health check
- `GET /health` - Detailed health status

### Matching
- `POST /match` - Find best candidate match
- `GET /matches/<id>` - Get match by ID
- `GET /matches` - Deprecated

### Roles
- `POST /post-role` - Submit role posting
- `GET /view-roles` - List all role postings

### Introductions
- `POST /request-intro` - Request introduction (recommended)
- `POST /send_intro` - Send intro email (legacy/deprecated)

### Feedback
- `POST /feedback` - Submit feedback

### Voice
- `POST /call_candidate` - Initiate outbound Twilio call
- `POST /voice/intro` - Twilio webhook (call start)
- `POST /voice/capture` - Twilio webhook (speech capture)

See `openapi.yaml` for full API documentation.

## Development

The codebase is organized using Flask blueprints for modular route handling. Each route module is self-contained and imports its dependencies from `config/`, `services/`, and `utils/`.

## Deployment

The `Procfile` is configured to run with Gunicorn:
```
web: gunicorn server:app --preload --workers 2 --threads 8 --timeout 120 -b 0.0.0.0:$PORT
```

