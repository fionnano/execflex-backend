"""Standard API response helpers."""
from flask import jsonify
from typing import Any, List, Optional


def api_ok(data: Any, status: int = 200):
    return jsonify({"ok": True, "data": data}), status


def api_error(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


def api_paginated(items: List[Any], total: int, page: int = 1, per_page: int = 50):
    return jsonify({
        "ok": True,
        "data": items,
        "pagination": {
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": (total + per_page - 1) // per_page,
        }
    }), 200
