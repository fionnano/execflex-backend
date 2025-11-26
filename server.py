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

# Routes
from routes import (
    health_bp,
    matching_bp,
    roles_bp,
    introductions_bp,
    feedback_bp,
    voice_bp
)

# Validate configuration
validate_config()
print_config_status()

# Create Flask app
app = Flask(__name__, static_folder="static")
CORS(app, resources={r"/*": {"origins": "*"}})

# Register blueprints
app.register_blueprint(health_bp)
app.register_blueprint(matching_bp)
app.register_blueprint(roles_bp)
app.register_blueprint(introductions_bp)
app.register_blueprint(feedback_bp)
app.register_blueprint(voice_bp)

# Pre-cache common TTS prompts at startup
pre_cache_common_prompts()

# Debug: Print registered routes at startup
with app.app_context():
    print("DEBUG Registered routes at startup:")
    for rule in app.url_map.iter_rules():
        print(" -", rule)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=True)
