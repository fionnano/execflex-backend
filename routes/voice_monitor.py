"""
Cara voice uptime monitor.

Runs a synthetic probe every 5 minutes that exercises the full voice path:
  POST /voice-session/cara -> WebSocket connect -> OpenAI handshake -> audio chunk

Alerts fionnan@ainm.ai on RED (2+ consecutive failures) and on recovery.
Exposes state via GET /health/voice/monitor.
"""
import json
import os
import ssl
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Optional

from flask import Blueprint, jsonify

monitor_bp = Blueprint("voice_monitor", __name__)

_PROBE_INTERVAL = 300  # 5 minutes
_ALERT_EMAIL = "fionnan@ainm.ai"
_ALERT_THROTTLE = 1800  # 30 minutes between emails
_MAX_RESULTS = 20
_AUDIO_TIMEOUT = 10  # seconds to wait for first audio chunk

# ── State ────────────────────────────────────────────────────────────────────

_results: deque = deque(maxlen=_MAX_RESULTS)
_lock = threading.Lock()
_last_alert_time: float = 0
_last_alert_status: Optional[str] = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _status() -> str:
    with _lock:
        if len(_results) < 2:
            return "unknown"
        last_two = [_results[-1], _results[-2]]
        if all(r["ok"] for r in last_two):
            return "green"
        if all(not r["ok"] for r in last_two):
            return "red"
        return "amber"


def _success_rate_24h() -> float:
    now = time.time()
    with _lock:
        recent = [r for r in _results if now - r["ts"] < 86400]
    if not recent:
        return 0.0
    return sum(1 for r in recent if r["ok"]) / len(recent)


# ── Probe ────────────────────────────────────────────────────────────────────

def _run_probe() -> dict:
    """Execute one synthetic voice probe. Returns result dict."""
    t0 = time.monotonic()
    result = {"ts": time.time(), "time": _now_iso(), "ok": False, "error": None, "steps": []}

    import requests
    try:
        import websocket as ws_client
    except ImportError:
        result["error"] = "websocket-client not installed"
        return result

    base_url = os.getenv("EXECFLEX_BASE_URL", "https://execflex-backend-1.onrender.com").rstrip("/")
    service_key = os.getenv("AINM_SERVICE_KEY")

    # Step 1: Create session
    try:
        headers = {"Content-Type": "application/json"}
        if service_key:
            headers["X-Service-Key"] = service_key
        resp = requests.post(
            f"{base_url}/voice-session/cara",
            json={"system_prompt": "You are a test assistant. Say hello."},
            headers=headers,
            timeout=10,
        )
        if resp.status_code != 201:
            result["error"] = f"POST /voice-session/cara returned {resp.status_code}: {resp.text[:200]}"
            result["steps"].append("session_create_failed")
            return result
        session_data = resp.json()
        ws_url = session_data.get("ws_url")
        if not ws_url:
            result["error"] = "No ws_url in session response"
            return result
        result["steps"].append("session_created")
    except Exception as e:
        result["error"] = f"Session create: {type(e).__name__}: {e}"
        return result

    # Step 2: Connect WebSocket
    probe_ws = None
    try:
        probe_ws = ws_client.create_connection(
            ws_url,
            timeout=15,
            skip_utf8_validation=True,
            sslopt={"cert_reqs": ssl.CERT_REQUIRED},
        )
        result["steps"].append("ws_connected")
    except Exception as e:
        result["error"] = f"WS connect: {type(e).__name__}: {e}"
        return result

    # Step 3: Wait for session_started + audio
    try:
        saw_session_started = False
        saw_audio = False
        deadline = time.monotonic() + _AUDIO_TIMEOUT

        while time.monotonic() < deadline:
            probe_ws.settimeout(max(0.5, deadline - time.monotonic()))
            try:
                raw = probe_ws.recv()
            except Exception:
                break
            if not raw:
                break
            msg = json.loads(raw)
            msg_type = msg.get("type", "")

            if msg_type == "session_started":
                saw_session_started = True
                result["steps"].append("session_started")

            elif msg_type == "audio":
                saw_audio = True
                result["steps"].append("audio_received")
                break

            elif msg_type == "error":
                result["error"] = f"Server error: {msg.get('message', '?')}"
                return result

        if not saw_session_started:
            result["error"] = "Timeout waiting for session_started"
            return result
        if not saw_audio:
            result["error"] = "Timeout waiting for audio chunk"
            return result

        result["ok"] = True
        result["latency_ms"] = int((time.monotonic() - t0) * 1000)
        result["steps"].append("probe_passed")

    except Exception as e:
        result["error"] = f"Probe loop: {type(e).__name__}: {e}"
    finally:
        try:
            if probe_ws:
                probe_ws.send(json.dumps({"type": "end"}))
                probe_ws.close()
        except Exception:
            pass

    return result


# ── Alerting ─────────────────────────────────────────────────────────────────

def _send_alert(status: str) -> None:
    global _last_alert_time, _last_alert_status
    now = time.time()

    if status == _last_alert_status and (now - _last_alert_time) < _ALERT_THROTTLE:
        print(f"[VoiceMonitor] Alert throttled ({status})", flush=True)
        return

    from modules.email_sender import _admin_alert, EMAIL_ADDRESS, EMAIL_PASSWORD, _send_message
    from email.message import EmailMessage
    from email.utils import formataddr

    if not EMAIL_ADDRESS or not EMAIL_PASSWORD:
        print(f"[VoiceMonitor] Email not configured, cannot alert", flush=True)
        return

    if status == "red":
        with _lock:
            failures = [r for r in _results if not r["ok"]][-3:]
        failure_lines = []
        for f in failures:
            failure_lines.append(f"  {f['time']}  {f.get('error', '?')}")

        msg = EmailMessage()
        msg["From"] = formataddr(("ExecFlex Voice Monitor", EMAIL_ADDRESS))
        msg["To"] = _ALERT_EMAIL
        msg["Subject"] = "Cara voice DOWN"
        msg.set_content("\n".join([
            "Cara voice probe has failed 2+ consecutive times.",
            "",
            "Recent failures:",
            *failure_lines,
            "",
            f"Dashboard: {os.getenv('EXECFLEX_BASE_URL', 'https://execflex-backend-1.onrender.com')}/health/voice/monitor",
            f"Checked at {_now_iso()}",
        ]))

        try:
            _send_message(msg)
            print(f"[VoiceMonitor] DOWN alert sent to {_ALERT_EMAIL}", flush=True)
            _last_alert_time = now
            _last_alert_status = status
        except Exception as e:
            print(f"[VoiceMonitor] Alert send failed: {e}", flush=True)

    elif status == "green" and _last_alert_status == "red":
        msg = EmailMessage()
        msg["From"] = formataddr(("ExecFlex Voice Monitor", EMAIL_ADDRESS))
        msg["To"] = _ALERT_EMAIL
        msg["Subject"] = "Cara voice RECOVERED"
        msg.set_content("\n".join([
            "Cara voice probe is passing again.",
            "",
            f"Recovered at {_now_iso()}",
            f"Dashboard: {os.getenv('EXECFLEX_BASE_URL', 'https://execflex-backend-1.onrender.com')}/health/voice/monitor",
        ]))

        try:
            _send_message(msg)
            print(f"[VoiceMonitor] RECOVERED alert sent to {_ALERT_EMAIL}", flush=True)
            _last_alert_time = now
            _last_alert_status = status
        except Exception as e:
            print(f"[VoiceMonitor] Recovery alert send failed: {e}", flush=True)


# ── Loop ─────────────────────────────────────────────────────────────────────

def _monitor_loop():
    time.sleep(30)  # let server finish starting
    print("[VoiceMonitor] Starting (interval=300s)", flush=True)
    while True:
        try:
            result = _run_probe()
            with _lock:
                _results.append(result)
            status = _status()
            print(f"[VoiceMonitor] probe={'PASS' if result['ok'] else 'FAIL'} "
                  f"status={status} steps={result['steps']} "
                  f"error={result.get('error', '-')}", flush=True)

            if status == "red":
                _send_alert("red")
            elif status == "green":
                _send_alert("green")

        except Exception as e:
            print(f"[VoiceMonitor] Loop error: {e}", flush=True)

        time.sleep(_PROBE_INTERVAL)


_monitor_thread = threading.Thread(target=_monitor_loop, daemon=True)
_monitor_thread.start()


# ── Health endpoint ──────────────────────────────────────────────────────────

@monitor_bp.route("/health/voice/monitor", methods=["GET"])
def voice_monitor_health():
    with _lock:
        recent = list(_results)

    status = _status()
    last_success = next((r for r in reversed(recent) if r["ok"]), None)
    last_failure = next((r for r in reversed(recent) if not r["ok"]), None)

    return jsonify({
        "status": status,
        "last_check": recent[-1]["time"] if recent else None,
        "last_success": last_success["time"] if last_success else None,
        "last_failure": last_failure["time"] if last_failure else None,
        "success_rate_24h": round(_success_rate_24h(), 3),
        "recent_results": [
            {
                "time": r["time"],
                "ok": r["ok"],
                "error": r.get("error"),
                "steps": r["steps"],
                "latency_ms": r.get("latency_ms"),
            }
            for r in reversed(recent)
        ],
    }), 200
