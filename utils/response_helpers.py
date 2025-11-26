"""
Flask response helper functions.
"""
from flask import jsonify


def ok(payload=None, status=200, **extra):
    """Create a successful JSON response."""
    data = {"ok": True}
    if payload:
        data.update(payload)
    if extra:
        data.update(extra)
    return jsonify(data), status


def bad(message, status=400, **extra):
    """Create an error JSON response."""
    data = {"ok": False, "error": message}
    if extra:
        data.update(extra)
    return jsonify(data), status

