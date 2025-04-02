# modules/respond.py

import os
from openai import OpenAI

OPENAI_API_KEY = "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"
client = OpenAI(api_key=OPENAI_API_KEY)

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

    response = client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": facts_prompt + "\n\n" + user_input}
        ],
        temperature=0.6,
        max_tokens=300
    )

    return response.choices[0].message.content
