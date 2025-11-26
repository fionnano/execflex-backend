"""
GPT service for natural conversation rephrasing.
"""
from config.clients import gpt_client, OPENAI_API_KEY


def rephrase(context: str, fallback: str) -> str:
    """
    Use GPT to make Ai-dan's reply more natural and human.
    - Keeps it short.
    - Does NOT invent new steps.
    Falls back to the scripted prompt if anything fails.
    
    Args:
        context: Current conversation context
        fallback: Default prompt text to use if GPT fails
        
    Returns:
        Rephrased prompt or fallback text
    """
    if not gpt_client or not OPENAI_API_KEY:
        return fallback
    
    try:
        prompt = (
            "You are Ai-dan, a friendly executive search consultant at ExecFlex. "
            "Rephrase the following system prompt in a short, natural, conversational way. "
            "Do NOT add new fields or steps. Keep it human and concise.\n\n"
            f"Context so far:\n{context}\n\nSystem Prompt:\n{fallback}"
        )
        resp = gpt_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are Ai-dan."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=80
        )
        out = resp.choices[0].message.content.strip()
        if not out:
            return fallback
        return out
    except Exception as e:
        print("⚠️ GPT rephrase failed:", e)
        return fallback

