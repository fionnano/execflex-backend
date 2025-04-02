# modules/match_finder.py

import json
import os

def find_best_match(match_type, role, industry, culture):
    file_path = os.path.join(os.path.dirname(__file__), "../data/matches.json")

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            matches = json.load(f)

        for match in matches:
            if (
                match.get("role", "").lower() == role.lower()
                and match.get("industry", "").lower() == industry.lower()
                and match.get("culture", "").lower() == culture.lower()
            ):
                return {
                    "name": match.get("name", "Unnamed"),
                    "summary": match.get("summary", "No summary provided."),
                    "email": match.get("email", "noemail@example.com")
                }

        return None

    except Exception as e:
        print("❌ Error reading matches.json:", e)
        return None
