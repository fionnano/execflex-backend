"""
GPT service for natural conversation rephrasing and interactive conversations.
"""
from typing import List, Dict, Optional
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


def generate_conversational_response(
    system_prompt: str,
    conversation_history: List[Dict[str, str]],
    user_input: str,
    context: Optional[Dict] = None,
    temperature: float = 0.8,
    max_tokens: int = 150
) -> str:
    """
    Generate an interactive conversational response using GPT.
    
    Args:
        system_prompt: System prompt defining the AI's role and behavior
        conversation_history: List of previous messages [{"role": "user/assistant", "content": "..."}]
        user_input: Current user input/speech
        context: Optional context dict with additional information
        temperature: GPT temperature (0.0-1.0)
        max_tokens: Maximum tokens in response
        
    Returns:
        Generated response text
    """
    if not gpt_client or not OPENAI_API_KEY:
        return ""
    
    try:
        # Build context string if provided
        context_str = ""
        if context:
            context_parts = []
            for key, value in context.items():
                if value:
                    context_parts.append(f"{key}: {value}")
            if context_parts:
                context_str = "\nAdditional Context:\n" + "\n".join(context_parts) + "\n"
        
        # Build messages
        messages = [
            {"role": "system", "content": system_prompt + context_str}
        ]
        
        # Add conversation history
        messages.extend(conversation_history)
        
        # Add current user input
        messages.append({"role": "user", "content": user_input})
        
        # Generate response
        resp = gpt_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens
        )
        
        out = resp.choices[0].message.content.strip()
        return out if out else ""
    except Exception as e:
        print(f"⚠️ GPT conversational response failed: {e}")
        return ""

