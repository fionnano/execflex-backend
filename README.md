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

## API Documentation

Complete API documentation is available in the OpenAPI 3.0 specification:

**📄 [`openapi.yaml`](./openapi.yaml)**

This file contains:
- All available endpoints with request/response schemas
- Authentication requirements
- Example requests and responses
- Error response formats

You can view the OpenAPI spec using tools like:
- [Swagger Editor](https://editor.swagger.io/) - Paste the YAML content
- [Redoc](https://redocly.com/reference-docs/redoc/) - Generate interactive docs
- Your IDE's OpenAPI preview (if supported)

## Architecture: When to Create Backend Endpoints

**IMPORTANT**: Follow this principle when adding new endpoints:

### ✅ Create Backend Endpoints For:
1. **Secret Credentials / API Keys**
   - Email sending (Gmail SMTP credentials)
   - Twilio voice calls (Twilio auth tokens)
   - External service integrations (ElevenLabs, OpenAI)

2. **Complex Business Logic**
   - Matching algorithms with scoring
   - Data transformation/validation before storage
   - Multi-step workflows

3. **External API Orchestration**
   - Combining multiple external services
   - Rate limiting and retry logic
   - Webhook handling

4. **Server-Side Processing**
   - File processing
   - Background jobs
   - Scheduled tasks

### ❌ Don't Create Backend Endpoints For:
- Simple CRUD operations (frontend should use direct Supabase)
- Auth validation alone (RLS handles this)
- Role checking (RLS + `has_role()` function handles this)
- Simple passthrough operations (just passing data to Supabase)

**Key Principle**: Use backend API only when additional business logic is needed that should be hidden from public view, or when secret credentials are required. For simple CRUD operations on user-owned data, the frontend should connect directly to Supabase with RLS policies enforcing access control.

**Example:**
```python
# ✅ GOOD: Backend endpoint with business logic
@roles_bp.route("/post-role", methods=["POST"])
def post_role():
    # 1. Validate auth & role
    # 2. Transform/clean data (business logic)
    # 3. Save to Supabase
    supabase_client.table("role_postings").insert(cleaned_data).execute()
```

```python
# ❌ BAD: Backend endpoint just for passthrough
@roles_bp.route("/update-profile", methods=["POST"])
def update_profile():
    # Just passes data through to Supabase - unnecessary!
    supabase_client.table("profiles").update(data).execute()
    # Frontend should do this directly with RLS protection
```

**See**: `docs/backend_vs_supabase_guidelines.md` for detailed guidelines and examples.

## Development

The codebase is organized using Flask blueprints for modular route handling. Each route module is self-contained and imports its dependencies from `config/`, `services/`, and `utils/`.

## Deployment

The `Procfile` is configured to run with Gunicorn:
```
web: gunicorn server:app --preload --workers 2 --threads 8 --timeout 120 -b 0.0.0.0:$PORT
```

