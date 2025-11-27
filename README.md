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

# Copy environment variables template and update with your values
cp .env.example .env
# Edit .env with your actual values (see .env.example for details)

# Run server
python server.py
```

## Configuration

### Local Development

1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and fill in your actual values. See `.env.example` for all available environment variables and their descriptions.

**Note:** `.env` is gitignored - never commit it to the repository.

### Environment Variables

All environment variables are documented in `.env.example`. For Render deployment, see `RENDER_ENV_VARS.md` for a complete checklist.

Environment variable loading is handled in `config/app_config.py`.

## API Endpoints

### Health
- `GET /` - Root health check
- `GET /health` - Detailed health status

### Matching
- `POST /match` - Find best candidate match

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

