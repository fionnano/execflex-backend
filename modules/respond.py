# modules/respond.py

import openai
import os

openai.api_key = os.getenv("OPENAI_API_KEY")

def generate_response(user_input, known_facts):
    system_prompt = (
        "You are Ai-dan, a friendly executive advisor at ExecFlex. "
        "You're on a relaxed call with a founder. Speak in a natural tone. "
        "Be concise, warm, and smart. "
        "Ask only about the missing info: role, industry, or team culture. "
        "If all 3 are already known, confirm them and get ready to suggest a great match."
    )

    facts_prompt = (
        f"Here's what I already know:\n"
        f"- Role: {known_facts.get('role') or 'Unknown'}\n"
        f"- Industry: {known_facts.get('industry') or 'Unknown'}\n"
        f"- Culture: {known_facts.get('culture') or 'Unknown'}"
    )

    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": facts_prompt + "\n\n" + user_input}
        ],
        temperature=0.6,
        max_tokens=300
    )

    return response.choices[0].message["content"]
