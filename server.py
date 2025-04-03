# server.py

import sys
import os

# Add the modules folder to the Python path so we can import from it
sys.path.append(os.path.join(os.path.dirname(__file__), "modules"))

from flask import Flask, request, jsonify
from match_finder import find_best_match
from email_sender import send_intro_email
from feedback_handler import save_feedback

# ✅ Define the Flask app BEFORE using @app.route
app = Flask(__name__)

# Health check route
@app.route("/", methods=["GET"])
def health_check():
    return "✅ Backend is live!", 200

@app.route("/match", methods=["POST"])
def match():
    data = request.json
    user_type = data.get("type")  # "client" or "candidate"
    name = data.get("name", "there")
    role = data.get("role")
    industry = data.get("industry")
    culture = data.get("culture")

    # Validate required info
    if not all([user_type, role, industry, culture]):
        return jsonify({"error": "Missing info"}), 400

    match = find_best_match(user_type, role, industry, culture)

    if match:
        return jsonify({
            "message": f"We recommend {match['name']}: {match['summary']}",
            "match": match
        })
    else:
        return jsonify({
            "message": "No match found yet. We'll follow up with suggestions soon.",
            "match": None
        })

@app.route("/send_intro", methods=["POST"])
def send_intro():
    try:
        print("🚀 /send_intro route was hit")
        data = request.get_json()
        print("📦 Incoming data:", data)

        client_name = data.get("client_name")
        match_name = "Matched Exec"  # Placeholder for testing
        email = data.get("email")

        print(f"🧪 Sending intro: {client_name} ↔ {match_name} to {email}")

        success = send_intro_email(client_name, match_name, email)

        return jsonify({"status": "success" if success else "fail"}), 200 if success else 500

    except Exception as e:
        print("❌ EXCEPTION in /send_intro route:", str(e))
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/feedback", methods=["POST"])
def feedback():
    data = request.json
    save_feedback(data["user"], data["match"], data["feedback"])
    return jsonify({"status": "saved"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
