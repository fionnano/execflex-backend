"""Input validation for the AI Act Check public forms."""
from __future__ import annotations

from typing import Any

from agentic_core.agents.compliance import get_question_set
from services.aiact.constants import MAX_NAME_LEN, MAX_ANSWER_LEN, MAX_ANSWERS, MAX_DESC_LEN


class ValidationError(ValueError):
    """Raised when a form field fails validation."""


# Build a lookup of question id → allowed option values (for select questions).
def _question_index() -> dict[str, dict]:
    idx: dict[str, dict] = {}
    for stage in get_question_set():
        for q in stage.questions:
            idx[q.id] = {
                "kind": q.kind,
                "options": {o.value for o in q.options},
                "required": q.required,
            }
    return idx


_Q_INDEX = _question_index()


def clean_system_name(value: Any) -> str:
    if not isinstance(value, str):
        value = "" if value is None else str(value)
    value = value.strip()
    if not value:
        raise ValidationError("system_name is required")
    if len(value) > MAX_NAME_LEN:
        raise ValidationError(f"system_name must be at most {MAX_NAME_LEN} characters")
    return value


def validate_answers(answers: Any) -> dict:
    """Validate a flat answers dict against the question set.

    Unknown keys are dropped; select answers must be within allowed options;
    text answers are length-capped; multi-selects must be lists of valid values.
    """
    if not isinstance(answers, dict):
        raise ValidationError("answers must be an object")
    if len(answers) > MAX_ANSWERS:
        raise ValidationError("too many answers")

    out: dict[str, Any] = {}
    for key, val in answers.items():
        spec = _Q_INDEX.get(key)
        if spec is None:
            continue  # ignore unknown keys
        kind = spec["kind"]
        if kind == "text":
            s = ("" if val is None else str(val)).strip()
            cap = MAX_DESC_LEN if key == "system_description" else MAX_ANSWER_LEN
            out[key] = s[:cap]
        elif kind == "multi_select":
            if val in (None, ""):
                out[key] = []
                continue
            if isinstance(val, str):
                val = [val]
            if not isinstance(val, list):
                raise ValidationError(f"{key} must be a list")
            cleaned = [v for v in val if v in spec["options"]]
            out[key] = cleaned
        elif kind in ("single_select", "boolean"):
            if val in (None, ""):
                continue
            if val not in spec["options"]:
                raise ValidationError(f"{key} must be one of {sorted(spec['options'])}")
            out[key] = val
        else:
            out[key] = val
    return out
