# modules/match_finder.py

import json
import os

def find_best_match(match_type, role, industry, culture):
    try:
        # Path to the local matches.json file
        matches_path = os.path.join(os.path.dirname(__file__), '../data/matches.json')

        # Load match data
        with open(matches_path, "r") as f:
            matches = json.load(f)

        # Find the first match that meets all criteria (case-insensitive)
        for match in matches:
            if (
                match.get("role", "").lower() == role.lower() and
                match.get("industry", "").lower() == industry.lower() and
                match.get("culture", "").lower() == culture.lower()
            ):
                return {
                    "name": match.get("name", "Unnamed"),
                    "summary": match.get("summary", "No summary provided."),
                    "email": match.get("email", "noemail@example.com")
                }

        return None

    except Exception as e:
        print("❌ Error in find_best_match:", e)
        return None
