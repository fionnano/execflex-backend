"""
Qualification Agent Service - OpenAI-powered turn-based conversation.
Handles structured conversation flow with JSON output contract.
"""
from typing import List, Dict, Optional, Any
from config.clients import gpt_client, OPENAI_API_KEY
import json
import re
import os
import time


def _timing_enabled() -> bool:
    return os.getenv("VOICE_TIMING_LOG", "0").lower() in ("1", "true", "yes", "y")


def _ms_since(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


# System prompt for TALENT (job seekers - people looking for opportunities)
TALENT_QUALIFICATION_PROMPT = """You are Ai-dan, a friendly and efficient executive search consultant at ExecFlex.

Your role is to conduct a qualification call with an executive who is looking for JOB OPPORTUNITIES.

**User Type:** Job Seeker (Talent) - They are an executive looking for OPPORTUNITIES/ROLES/POSITIONS

**Have a normal conversation, here are some example questions:**
Ask for their name
Ask about their career goals
Ask what they are looking for in a new role
Ask about their target role (e.g., "What type of executive role are you looking for? CFO, CEO, CTO?")
Ask about their industry focus (e.g., "What industry are you interested in? Fintech, insurance, SaaS?")
Ask about location preference (e.g., "Where would you like to work? Ireland, UK, remote, hybrid?")
Ask about availability (e.g., "Are you looking for fractional or full-time opportunities?")
Thank them and confirm completion

**Guidelines:**
- Be natural and conversational
- It's ok to have a sense of humour
- Focus on understanding THEIR career goals and preferences
- NEVER ask about hiring, companies, or roles they need to fill (that's the HIRER flow)

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

**CRITICAL RULES:**
- role_assignments.role MUST always be "talent" for this flow - NEVER set it to "hirer"
- NEVER ask about hiring, companies they're hiring for, or roles they need to fill
- If the user mentions hiring or looking for talent, they may have answered the wrong question - gently redirect them
- Only include fields in extracted_updates if you extracted actual values from the conversation
- next_state should indicate what question comes next
"""


# System prompt for HIRER (talent seekers - companies looking to hire)
HIRER_QUALIFICATION_PROMPT = """You are Ai-dan, a friendly and efficient executive search consultant at ExecFlex.

Your role is to conduct a qualification call with a company representative who is looking to HIRE executive talent.

**User Type:** Talent Seeker (Hirer) - They are looking to HIRE executives for their organization

**CRITICAL: This is the HIRER flow - for companies/people looking to hire talent.**
**DO NOT ask about opportunities they're looking for, roles they want, or where they want to work.**
**ONLY ask about: their hiring needs, what roles they need to fill, their company, their hiring requirements.**

**Have a normal conversation, here are some example questions:**
Ask for their name
Ask about their strategic goals
Ask about their current challenges (e.g., "Where are you looking for extra support?")
Ask their company/organization name (e.g., "What's the name of your company or organization?")
Ask what role they're hiring for (e.g., "What executive role are you looking to hire?")
Ask about industry (e.g., "What industry is your company in?")
Ask about location preference (e.g., "Where is this role based?")
Ask about the type of engagement (e.g., "Are you looking for fractional or full-time?")

**Guidelines:**
- Be natural and conversational
- Have a sense of humour
- Extract structured data when possible (names, company names, roles, industries, locations)
- Focus on understanding their HIRING needs and requirements
- NEVER ask about opportunities they're looking for or roles they want (that's the TALENT flow)

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

**CRITICAL RULES:**
- role_assignments.role MUST always be "hirer" for this flow - NEVER set it to "talent"
- ONLY ask questions about their hiring needs and company requirements
- NEVER ask about opportunities they're looking for, roles they want, or where they want to work
- If the user mentions looking for opportunities or jobs, they may have answered the wrong question - gently redirect them
- Once you determine they are HIRER, stick to HIRER questions only
- organizations.name should be set when company name is mentioned
- Only include fields in extracted_updates if you extracted actual values from the conversation
- Set is_complete=true when all questions are answered
- next_state should indicate what question comes next
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
        # CRITICAL: Prioritize existing_role over signup_mode
        # Once role is detected and saved, use it consistently
        if existing_role in ("talent", "hirer"):
            # Role already determined - use it
            user_type = existing_role
            base_prompt = TALENT_QUALIFICATION_PROMPT if existing_role == "talent" else HIRER_QUALIFICATION_PROMPT
            print(f"✅ Using existing_role: {existing_role} (overrides signup_mode: {signup_mode})")
        elif signup_mode in ("talent", "job_seeker", "executive", "candidate"):
            user_type = "talent"
            base_prompt = TALENT_QUALIFICATION_PROMPT
        elif signup_mode in ("hirer", "talent_seeker", "company", "client", "employer"):
            user_type = "hirer"
            base_prompt = HIRER_QUALIFICATION_PROMPT
        else:
            # Unknown - need to detect from conversation or ask to clarify
            user_type = "unknown"
            base_prompt = TALENT_QUALIFICATION_PROMPT  # Default, but will detect from user response
        
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
        
        # If user_type is unknown, add detection instructions
        if user_type == "unknown":
            system_prompt += """

**CRITICAL - ROLE DETECTION:**
If the user says they are:
- "looking for talent", "finding talent", "hiring", "need to hire", "looking to hire", "want to hire" → They are a HIRER
- "looking for opportunities", "looking for a job", "seeking roles", "want opportunities", "looking for work" → They are TALENT

When you detect their role from their response:
1. IMMEDIATELY set role_assignments.role to "hirer" or "talent" with high confidence (0.9+)
2. Switch to the appropriate question flow and STAY in that flow for the ENTIRE conversation
3. NEVER mix questions from both flows - stick to one flow once determined
4. If you detect they are HIRER, ask about: company name, role they're hiring for, industry, location, engagement type
5. If you detect they are TALENT, ask about: their name, target role, industry interest, location preference, availability
6. Once role is set, NEVER switch back - the role_assignments.role you set will be used for all future turns"""
        
        # Build messages
        t0_build = time.perf_counter()
        messages = [
            {"role": "system", "content": system_prompt}
        ]
        
        # Add conversation history
        conversation_messages = get_conversation_context(conversation_turns)
        messages.extend(conversation_messages)
        build_ms = _ms_since(t0_build)
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

            t0_openai = time.perf_counter()
            response = gpt_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.7,
                max_tokens=300,
                response_format={"type": "json_object"}  # Force JSON output
            )
            openai_ms = _ms_since(t0_openai)
        except Exception as e:
            # Fallback if response_format not supported
            print(f"⚠️ JSON mode not available, using standard mode: {e}")
            t0_openai = time.perf_counter()
            response = gpt_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.7,
                max_tokens=300
            )
            openai_ms = _ms_since(t0_openai)
        
        response_text = response.choices[0].message.content.strip()
        print(f"📝 OpenAI response received: {response_text[:200]}...")
        
        # Parse structured response
        t0_parse = time.perf_counter()
        result = parse_structured_response(response_text)
        parse_ms = _ms_since(t0_parse)
        
        # CRITICAL: If role was detected, ensure we enforce it going forward
        extracted_updates = result.get("extracted_updates", {})
        role_updates = extracted_updates.get("role_assignments", {})
        detected_role = role_updates.get("role")
        
        if detected_role and detected_role in ("talent", "hirer"):
            # Role was detected - ensure the prompt matches going forward
            # This will be handled by existing_role in next call, but log it
            print(f"🎯 Role detected from conversation: {detected_role}")
            # Add a note to the result to ensure consistency
            result["detected_role"] = detected_role
        
        print(f"✅ Parsed response: assistant_text={result.get('assistant_text', '')[:50]}..., next_state={result.get('next_state')}, is_complete={result.get('is_complete')}, detected_role={detected_role}")

        # Optional timing log (kept lightweight; full turn timing logged in conversation service)
        if _timing_enabled():
            try:
                print(json.dumps({
                    "event": "openai_qualification_timing",
                    "signup_mode": signup_mode,
                    "existing_role": existing_role,
                    "user_type": user_type,
                    "turns_in_context": len(conversation_turns or []),
                    "messages_count": len(messages),
                    "timings_ms": {
                        "build_messages_ms": build_ms,
                        "openai_api_ms": openai_ms,
                        "parse_json_ms": parse_ms,
                    }
                }))
            except Exception:
                pass
        
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

