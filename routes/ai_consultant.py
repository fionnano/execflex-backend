"""
AI Recruitment Consultant endpoint.

POST /ai/consultant — GPT-4o-powered chat endpoint used by the
frontend "AI Consultant" side panel. Hiring managers can ask
questions about their open roles or shortlisted candidates and
get commercially-minded recruitment advice tuned to the Irish and
wider European executive market.
"""
import threading
import time
from typing import Optional

from flask import Blueprint, request, jsonify

from utils.auth_helpers import require_auth


ai_consultant_bp = Blueprint("ai_consultant", __name__)


# ── Per-user rate limiting (20 requests per hour, sliding window) ───────────
# Same pattern as routes/screening.py — in-memory, process-local. Fine for
# the current single-worker deployment; would need Redis if we scale out.
_RATE_LIMIT = 20
_RATE_WINDOW_S = 3600
_rate_buckets: dict = {}
_rate_lock = threading.Lock()


def _check_rate(user_id: str) -> bool:
    """Return True if within the limit, False if exceeded."""
    now = time.time()
    cutoff = now - _RATE_WINDOW_S
    with _rate_lock:
        timestamps = [t for t in _rate_buckets.get(user_id, []) if t > cutoff]
        if len(timestamps) >= _RATE_LIMIT:
            _rate_buckets[user_id] = timestamps
            return False
        timestamps.append(now)
        _rate_buckets[user_id] = timestamps
        return True


# ── System prompt ────────────────────────────────────────────────────────────

_BASE_SYSTEM_PROMPT = (
    "You are an expert executive recruitment consultant specialising in the "
    "Irish and European market, working for ExecFlex / ai·nm search. You "
    "help hiring managers find and assess senior talent.\n\n"
    "Be direct, specific, commercially minded. Give concrete recommendations. "
    "You know the Irish executive market well — typical salary ranges "
    "(CFO Dublin €120-180k, VP Sales €90-140k, CTO €130-200k, CMO €100-160k), "
    "notice periods (typically 1-3 months for senior roles), market "
    "availability patterns.\n"
)

_CLOSING_GUIDANCE = (
    "\nKeep responses under 150 words unless detail is essential. "
    "Use bullet points. Be specific to the Irish/EU executive market."
)


def _build_system_prompt(
    role_context: Optional[dict],
    candidate_context: Optional[list],
) -> str:
    """Compose the system prompt with optional role + candidate sections."""
    parts = [_BASE_SYSTEM_PROMPT]

    if isinstance(role_context, dict) and role_context:
        title = (role_context.get("title") or "").strip()
        industry = (role_context.get("industry") or "").strip()
        location = (role_context.get("location") or "").strip()
        commitment = (role_context.get("commitment") or "").strip()
        lines = ["\nCurrent role the user is hiring for:"]
        if title:
            lines.append(f"- Title: {title}")
        if industry:
            lines.append(f"- Industry: {industry}")
        if location:
            lines.append(f"- Location: {location}")
        if commitment:
            lines.append(f"- Commitment: {commitment}")
        if len(lines) > 1:
            parts.append("\n".join(lines))

    if isinstance(candidate_context, list) and candidate_context:
        cand_lines = ["\nCurrent shortlist of candidates you can reference:"]
        # Cap at 10 candidates to keep the prompt lean
        for c in candidate_context[:10]:
            if not isinstance(c, dict):
                continue
            name = (c.get("name") or "Unknown").strip()
            headline = (c.get("headline") or "").strip()
            score = c.get("score")
            recommendation = (c.get("recommendation") or "").strip()
            bits = [name]
            if headline:
                bits.append(headline)
            if score is not None:
                bits.append(f"score {score}")
            if recommendation:
                bits.append(recommendation)
            cand_lines.append(f"- {' — '.join(bits)}")
        if len(cand_lines) > 1:
            parts.append("\n".join(cand_lines))

    parts.append(_CLOSING_GUIDANCE)
    return "".join(parts)


# ── Validation ───────────────────────────────────────────────────────────────

_ALLOWED_ROLES = {"user", "assistant"}
_MAX_MESSAGES = 20
_MAX_MESSAGE_CHARS = 2000


def _validate_messages(messages) -> Optional[str]:
    """Return an error string, or None if the messages array is valid."""
    if not isinstance(messages, list):
        return "messages must be an array"
    if not messages:
        return "messages array cannot be empty"
    if len(messages) > _MAX_MESSAGES:
        return f"messages array cannot contain more than {_MAX_MESSAGES} items"
    for idx, m in enumerate(messages):
        if not isinstance(m, dict):
            return f"messages[{idx}] must be an object"
        role = m.get("role")
        if role not in _ALLOWED_ROLES:
            return f"messages[{idx}].role must be 'user' or 'assistant'"
        content = m.get("content")
        if not isinstance(content, str) or not content.strip():
            return f"messages[{idx}].content must be a non-empty string"
        if len(content) > _MAX_MESSAGE_CHARS:
            return f"messages[{idx}].content exceeds {_MAX_MESSAGE_CHARS} chars"
    return None


# ── Route ────────────────────────────────────────────────────────────────────

@ai_consultant_bp.route("/ai/consultant", methods=["POST"])
@require_auth
def ai_consultant():
    """
    POST /ai/consultant

    Body (JSON):
      messages:          [{role: 'user'|'assistant', content: string}, ...]
      role_context:      {title, industry, location, commitment} (optional)
      candidate_context: [{name, headline, score, recommendation}] (optional)

    Returns:
      200 {"response": str, "tokens_used": int}
      400 {"error": "..."}                — invalid body
      429 {"error": "Too many requests"}  — rate limit
      502 {"error": "AI service error"}   — OpenAI error
      504 {"error": "AI consultant unavailable"}  — timeout
      503 {"error": "..."}                — OpenAI client not configured
    """
    user_id = request.environ.get("authenticated_user_id") or "unknown"

    # Per-user rate limit
    if not _check_rate(user_id):
        return jsonify({"error": "Too many requests"}), 429

    data = request.get_json(force=True, silent=True) or {}
    messages = data.get("messages")

    err = _validate_messages(messages)
    if err:
        return jsonify({"error": err}), 400

    role_context = data.get("role_context") if isinstance(data.get("role_context"), dict) else None
    candidate_context = data.get("candidate_context") if isinstance(data.get("candidate_context"), list) else None

    # Lazy-import the shared OpenAI client so a missing module doesn't
    # crash the blueprint registration at import time.
    try:
        from config.clients import gpt_client
    except Exception as e:
        print(f"[AI-CONSULTANT] Failed to import gpt_client: {e}", flush=True)
        return jsonify({"error": "AI consultant unavailable"}), 503

    if gpt_client is None:
        return jsonify({"error": "AI consultant not configured"}), 503

    system_prompt = _build_system_prompt(role_context, candidate_context)

    # Build the OpenAI messages array
    openai_messages = [{"role": "system", "content": system_prompt}]
    for m in messages:
        openai_messages.append({
            "role": m["role"],
            "content": m["content"],
        })

    try:
        resp = gpt_client.chat.completions.create(
            model="gpt-4o",
            messages=openai_messages,
            max_tokens=400,
            temperature=0.7,
            timeout=30,
        )
    except Exception as e:
        # OpenAI SDK raises subclasses of openai.APIError for various
        # failures. We don't want to import the SDK class hierarchy
        # here just for isinstance checks — match on the class name
        # instead, which keeps this file dependency-free.
        exc_name = type(e).__name__
        print(f"[AI-CONSULTANT] OpenAI error ({exc_name}): {e}", flush=True)
        if "Timeout" in exc_name or "timeout" in str(e).lower():
            return jsonify({"error": "AI consultant unavailable"}), 504
        return jsonify({"error": "AI service error"}), 502

    try:
        choice = resp.choices[0]
        content = (choice.message.content or "").strip()
        tokens_used = getattr(resp.usage, "total_tokens", 0) if getattr(resp, "usage", None) else 0
    except Exception as e:
        print(f"[AI-CONSULTANT] Failed to parse OpenAI response: {e}", flush=True)
        return jsonify({"error": "AI service error"}), 502

    print(
        f"[AI-CONSULTANT] user={user_id} tokens={tokens_used} messages={len(messages)}",
        flush=True,
    )

    return jsonify({
        "response": content,
        "tokens_used": tokens_used,
    }), 200
