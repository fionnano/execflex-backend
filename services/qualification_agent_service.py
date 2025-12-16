"""
Qualification Agent Service - OpenAI-powered turn-based conversation.
Handles structured conversation flow with JSON output contract.
"""
from typing import List, Dict, Optional, Any
from config.clients import gpt_client, OPENAI_API_KEY
import json
import re


# System prompt for TALENT (job seekers - people looking for opportunities)
TALENT_QUALIFICATION_PROMPT = """You are Ai-dan, a friendly and efficient executive search consultant at ExecFlex.

Your role is to conduct a qualification call with an executive who is looking for job opportunities.

**User Type:** Job Seeker (Talent) - They are an executive looking for opportunities

**Question Sequence:**
1. Welcome and confirm they're looking for opportunities (if not already known)
2. Ask for their first name
3. Ask about their target role (e.g., CFO, CEO, CTO, CMO, CMO, CHRO, COO)
4. Ask about their industry focus (e.g., fintech, insurance, SaaS, healthcare, retail)
5. Ask about location preference (Ireland, UK, remote, hybrid)
6. Ask about availability (fractional vs full-time)
7. Thank them and confirm completion

**Guidelines:**
- Keep responses concise (1-2 sentences max)
- Ask one question at a time
- Be natural and conversational
- Extract structured data when possible (names, roles, industries, locations)
- Mark conversation as complete when all key questions are answered
- Focus on understanding their career goals and preferences

**Output Format:**
You MUST respond with valid JSON only, no other text. Use this exact structure:
{
  "assistant_text": "What to say next",
  "extracted_updates": {
    "people_profiles": {
      "first_name": "value or null",
      "last_name": "value or null",
      "headline": "value or null",
      "location": "value or null (e.g., 'UK', 'Ireland', 'Remote', 'Hybrid')"
    },
    "role_assignments": {
      "role": "talent",
      "confidence": 0.0-1.0
    }
  },
  "next_state": "name|role|industry|location|availability|complete",
  "is_complete": false,
  "confidence": 0.0-1.0
}

**Important:**
- Only include fields in extracted_updates if you extracted actual values from the conversation
- Set is_complete=true when all questions are answered
- next_state should indicate what question comes next
- role_assignments.role should always be "talent" for this flow
"""


# System prompt for HIRER (talent seekers - companies looking to hire)
HIRER_QUALIFICATION_PROMPT = """You are Ai-dan, a friendly and efficient executive search consultant at ExecFlex.

Your role is to conduct a qualification call with a company representative who is looking to hire executive talent.

**User Type:** Talent Seeker (Hirer) - They are looking to hire executives

**Question Sequence:**
1. Welcome and confirm they're looking to hire (if not already known)
2. Ask for their first name
3. Ask about their company/organization name
4. Ask what role they're hiring for (e.g., CFO, CEO, CTO, CMO, CHRO, COO)
5. Ask about industry (e.g., fintech, insurance, SaaS, healthcare, retail)
6. Ask about location preference (Ireland, UK, remote, hybrid)
7. Ask about the type of engagement (fractional vs full-time)
8. Thank them and confirm completion

**Guidelines:**
- Keep responses concise (1-2 sentences max)
- Ask one question at a time
- Be natural and conversational
- Extract structured data when possible (names, company names, roles, industries, locations)
- Mark conversation as complete when all key questions are answered
- Focus on understanding their hiring needs and requirements

**Output Format:**
You MUST respond with valid JSON only, no other text. Use this exact structure:
{
  "assistant_text": "What to say next",
  "extracted_updates": {
    "people_profiles": {
      "first_name": "value or null",
      "last_name": "value or null",
      "headline": "value or null (role they're hiring for, e.g., 'CFO', 'CEO', 'CTO')",
      "location": "value or null (e.g., 'UK', 'Ireland', 'Remote', 'Hybrid')",
      "industries": "value or null (single industry string, e.g., 'fintech', 'SaaS', 'healthcare')"
    },
    "role_assignments": {
      "role": "hirer",
      "confidence": 0.0-1.0
    },
    "organizations": {
      "name": "value or null",
      "industry": "value or null (single industry string, e.g., 'fintech', 'SaaS', 'healthcare')",
      "location": "value or null (e.g., 'UK', 'Ireland', 'Remote', 'Hybrid')"
    },
    "role_postings": {
      "title": "value or null (role they're hiring for, e.g., 'CFO', 'CEO', 'CTO')",
      "location": "value or null (e.g., 'UK', 'Ireland', 'Remote', 'Hybrid')",
      "engagement_type": "value or null ('full_time', 'fractional', 'part_time', or 'contract')"
    }
  },
  "next_state": "name|company|role|industry|location|engagement|complete",
  "is_complete": false,
  "confidence": 0.0-1.0
}

**Important:**
- Only include fields in extracted_updates if you extracted actual values from the conversation
- Set is_complete=true when all questions are answered
- next_state should indicate what question comes next
- role_assignments.role should always be "hirer" for this flow
- organizations.name should be set when company name is mentioned
"""


def parse_structured_response(response_text: str) -> Dict[str, Any]:
    """
    Parse OpenAI response that should be JSON.
    Handles cases where response might have markdown code blocks or extra text.
    
    Returns:
        Parsed JSON dict with keys: assistant_text, extracted_updates, next_state, is_complete, confidence
    """
    if not response_text:
        return {
            "assistant_text": "I didn't catch that. Could you repeat?",
            "extracted_updates": {},
            "next_state": "unknown",
            "is_complete": False,
            "confidence": 0.0
        }
    
    # Try to extract JSON from markdown code blocks
    json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response_text, re.DOTALL)
    if json_match:
        response_text = json_match.group(1)
    else:
        # Try to find JSON object directly
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if json_match:
            response_text = json_match.group(0)
    
    try:
        parsed = json.loads(response_text)
        
        # Validate required fields
        result = {
            "assistant_text": parsed.get("assistant_text", "I didn't catch that. Could you repeat?"),
            "extracted_updates": parsed.get("extracted_updates", {}),
            "next_state": parsed.get("next_state", "unknown"),
            "is_complete": parsed.get("is_complete", False),
            "confidence": float(parsed.get("confidence", 0.5))
        }
        
        return result
    except json.JSONDecodeError as e:
        print(f"⚠️ Failed to parse JSON response: {e}")
        print(f"Response text: {response_text[:200]}")
        # Fallback response
        return {
            "assistant_text": "I didn't catch that. Could you repeat?",
            "extracted_updates": {},
            "next_state": "unknown",
            "is_complete": False,
            "confidence": 0.0
        }


def get_conversation_context(turns: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """
    Convert interaction_turns into OpenAI message format.
    
    Args:
        turns: List of turn dicts with keys: speaker, text, created_at
        
    Returns:
        List of messages in OpenAI format: [{"role": "user/assistant", "content": "..."}]
    """
    messages = []
    for turn in turns:
        role = "user" if turn.get("speaker") == "user" else "assistant"
        messages.append({
            "role": role,
            "content": turn.get("text", "")
        })
    return messages


def generate_qualification_response(
    conversation_turns: List[Dict[str, str]],
    signup_mode: Optional[str] = None,
    existing_profile: Optional[Dict] = None,
    existing_role: Optional[str] = None
) -> Dict[str, Any]:
    """
    Generate next assistant message and extract structured data using OpenAI.
    
    Uses different system prompts based on signup_mode:
    - "talent" or "job_seeker" → TALENT_QUALIFICATION_PROMPT (job seeker flow)
    - "hirer" or "talent_seeker" → HIRER_QUALIFICATION_PROMPT (talent seeker/hirer flow)
    - Unknown → Defaults to TALENT_QUALIFICATION_PROMPT but asks to clarify
    
    Args:
        conversation_turns: List of previous turns [{"speaker": "user/assistant", "text": "...", ...}]
        signup_mode: "talent" or "hirer" (from signup)
        existing_profile: Existing people_profiles data (if any)
        existing_role: Existing role_assignments role (if any)
        
    Returns:
        Dict with keys:
            - assistant_text: Next message to say
            - extracted_updates: Structured data to update in DB
            - next_state: Next question state
            - is_complete: Whether qualification is done
            - confidence: Confidence score (0.0-1.0)
    """
    if not gpt_client or not OPENAI_API_KEY:
        # Fallback response if OpenAI unavailable
        print(f"⚠️ OpenAI client not available: gpt_client={gpt_client is not None}, OPENAI_API_KEY={OPENAI_API_KEY is not None}")
        return {
            "assistant_text": "I'm having trouble processing that. Let's continue.",
            "extracted_updates": {},
            "next_state": "unknown",
            "is_complete": False,
            "confidence": 0.0
        }
    
    print(f"🤖 Generating qualification response:")
    print(f"   - conversation_turns: {len(conversation_turns)} turns")
    print(f"   - signup_mode: {signup_mode}")
    print(f"   - existing_profile: {existing_profile is not None}")
    print(f"   - existing_role: {existing_role}")
    
    try:
        # Determine which prompt to use based on signup_mode
        # Normalize signup_mode
        if signup_mode in ("talent", "job_seeker", "executive", "candidate"):
            user_type = "talent"
            base_prompt = TALENT_QUALIFICATION_PROMPT
        elif signup_mode in ("hirer", "talent_seeker", "company", "client", "employer"):
            user_type = "hirer"
            base_prompt = HIRER_QUALIFICATION_PROMPT
        else:
            # Unknown - default to talent but will ask to clarify
            user_type = "unknown"
            base_prompt = TALENT_QUALIFICATION_PROMPT
        
        # Build context string
        context_parts = []
        if signup_mode:
            context_parts.append(f"Signup mode: {signup_mode} (user type: {user_type})")
        if existing_profile:
            if existing_profile.get("first_name"):
                context_parts.append(f"Known first name: {existing_profile.get('first_name')}")
            if existing_profile.get("last_name"):
                context_parts.append(f"Known last name: {existing_profile.get('last_name')}")
        if existing_role:
            context_parts.append(f"Known role assignment: {existing_role}")
        
        context_str = "\n".join(context_parts) if context_parts else "No prior context"
        
        # Build system prompt with context
        system_prompt = base_prompt
        if context_str != "No prior context":
            system_prompt += f"\n\n**Current Context:**\n{context_str}"
        
        # Build messages
        messages = [
            {"role": "system", "content": system_prompt}
        ]
        
        # Add conversation history
        conversation_messages = get_conversation_context(conversation_turns)
        messages.extend(conversation_messages)
        print(f"   - Total messages (system + conversation): {len(messages)}")
        if conversation_messages:
            print(f"   - Last user message: {conversation_messages[-1].get('content', '')[:50]}...")
        else:
            print(f"   - No conversation history yet (this is the first turn)")
        
        # Generate response with JSON mode (if available) or structured output
        # Note: When using response_format={"type": "json_object"}, the system prompt
        # must explicitly instruct the model to return JSON
        try:
            # Try with response_format for structured output (OpenAI API v1.1+)
            # Ensure system prompt mentions JSON
            if "JSON" not in messages[0]["content"].upper():
                messages[0]["content"] += "\n\nIMPORTANT: You must respond with valid JSON only, no other text."
            
            response = gpt_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.7,
                max_tokens=300,
                response_format={"type": "json_object"}  # Force JSON output
            )
        except Exception as e:
            # Fallback if response_format not supported
            print(f"⚠️ JSON mode not available, using standard mode: {e}")
            response = gpt_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.7,
                max_tokens=300
            )
        
        response_text = response.choices[0].message.content.strip()
        print(f"📝 OpenAI response received: {response_text[:200]}...")
        
        # Parse structured response
        result = parse_structured_response(response_text)
        print(f"✅ Parsed response: assistant_text={result.get('assistant_text', '')[:50]}..., next_state={result.get('next_state')}, is_complete={result.get('is_complete')}")
        
        return result
        
    except Exception as e:
        print(f"❌ Qualification agent response generation failed: {e}")
        import traceback
        traceback.print_exc()
        print(f"   Debug info:")
        print(f"   - gpt_client available: {gpt_client is not None}")
        print(f"   - OPENAI_API_KEY available: {OPENAI_API_KEY is not None and len(OPENAI_API_KEY) > 0}")
        print(f"   - conversation_turns count: {len(conversation_turns) if conversation_turns else 0}")
        print(f"   - signup_mode: {signup_mode}")
        # Fallback response
        return {
            "assistant_text": "I'm having trouble processing that. Let's continue.",
            "extracted_updates": {},
            "next_state": "unknown",
            "is_complete": False,
            "confidence": 0.0
        }

