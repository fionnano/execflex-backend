"""
ExecFlex Combined API Server

Main entry point for the Flask application.
Handles both web API endpoints and voice/telephony features (Ai-dan).

See routes/ directory for endpoint implementations.
"""
import os
from flask import Flask
from flask_cors import CORS

# Configuration
from config.app_config import validate_config, print_config_status, PORT
from config.clients import supabase_client  # Initialize clients

# Services initialization
from services.tts_service import pre_cache_common_prompts

# Rate limiting
from utils.rate_limiting import create_limiter

# Routes
from routes import (
    health_bp,
    matching_bp,
    roles_bp,
    introductions_bp,
    voice_bp,
    onboarding_bp
)

# Validate configuration
validate_config()
print_config_status()

# Create Flask app
app = Flask(__name__, static_folder="static")
# Configure CORS to allow requests from frontend domain
# Flask-CORS will automatically handle OPTIONS preflight requests
CORS(app, resources={
    r"/*": {
        "origins": ["https://execflex.ai", "http://localhost:5173", "http://localhost:3000", "*"],
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
        "allow_headers": ["Content-Type", "Authorization", "X-Requested-With"],
        "expose_headers": ["Content-Type"],
        "supports_credentials": True,
        "max_age": 3600
    }
})

# Initialize rate limiter (IP-based)
limiter = create_limiter(app)

# Register blueprints
app.register_blueprint(health_bp)
app.register_blueprint(matching_bp)
app.register_blueprint(roles_bp)
app.register_blueprint(introductions_bp)
app.register_blueprint(voice_bp)
app.register_blueprint(onboarding_bp)

# Rate limiting can be applied to specific endpoints here if needed

# Pre-cache common TTS prompts at startup (optional)
# This can slow cold starts; enable only when desired.
if os.getenv("TTS_PRECACHE", "0").lower() in ("1", "true", "yes", "y"):
    pre_cache_common_prompts()

# Debug: Print registered routes at startup
with app.app_context():
    print("DEBUG Registered routes at startup:")
    for rule in app.url_map.iter_rules():
        print(" -", rule)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=True)
