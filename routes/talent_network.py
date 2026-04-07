"""
Public talent-network opt-in endpoint.

GET  /talent-network/join?ref=<code>  — public HTML landing page
POST /talent-network/join              — submission handler

Both are unauthenticated. The POST handler triggers a paid Twilio
call through create_screening_job, so it is rate-limited per-IP
and per-phone to prevent abuse.
"""
import re
import threading
import time
import traceback
from datetime import datetime, timezone
from typing import Optional

from flask import Blueprint, Response, request, jsonify

from config.clients import supabase_client


talent_network_bp = Blueprint("talent_network", __name__)


# ── Rate limiting (public endpoint — protects paid Twilio calls) ─────────────
# Two windows:
#   - per-IP:    max 3 submissions per 10 minutes
#   - per-phone: max 1 submission per hour
# Same in-memory sliding-window pattern as routes/screening.py. Process-
# local — not cluster-safe, fine for a single gunicorn worker.
_IP_LIMIT = 3
_IP_WINDOW_S = 600
_ip_buckets: dict = {}
_ip_lock = threading.Lock()

_PHONE_LIMIT = 1
_PHONE_WINDOW_S = 3600
_phone_buckets: dict = {}
_phone_lock = threading.Lock()


def _check_ip_rate(ip: str) -> bool:
    now = time.time()
    cutoff = now - _IP_WINDOW_S
    with _ip_lock:
        ts = [t for t in _ip_buckets.get(ip, []) if t > cutoff]
        if len(ts) >= _IP_LIMIT:
            _ip_buckets[ip] = ts
            return False
        ts.append(now)
        _ip_buckets[ip] = ts
        return True


def _check_phone_rate(phone: str) -> bool:
    now = time.time()
    cutoff = now - _PHONE_WINDOW_S
    with _phone_lock:
        ts = [t for t in _phone_buckets.get(phone, []) if t > cutoff]
        if len(ts) >= _PHONE_LIMIT:
            _phone_buckets[phone] = ts
            return False
        ts.append(now)
        _phone_buckets[phone] = ts
        return True


# ── Phone normalisation (Irish / UK heuristics) ──────────────────────────────
# Duplicated from routes/upload.py to avoid cross-importing between route
# modules. Same rules. Keep in sync.

def _normalise_phone(raw) -> Optional[str]:
    if not isinstance(raw, str):
        raw = str(raw) if raw is not None else ""
    cleaned = re.sub(r"[\s\-().]", "", raw.strip())
    if not cleaned:
        return None

    if cleaned.startswith("+"):
        return cleaned if re.fullmatch(r"\+\d{8,15}", cleaned) else None
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]
        return cleaned if re.fullmatch(r"\+\d{8,15}", cleaned) else None
    # Irish mobile 08X
    if re.fullmatch(r"08\d{7,9}", cleaned):
        return "+353" + cleaned[1:]
    # UK mobile 07X
    if re.fullmatch(r"07\d{9}", cleaned):
        return "+44" + cleaned[1:]
    # Bare digits — best effort
    if re.fullmatch(r"\d{8,15}", cleaned):
        return "+" + cleaned
    return None


_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _is_valid_email(s) -> bool:
    return isinstance(s, str) and bool(_EMAIL_RE.match(s.strip()))


def _get_client_ip() -> str:
    """Resolve the real client IP behind Render's proxy."""
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "unknown"


# ── HTML landing page ────────────────────────────────────────────────────────

_LANDING_HTML = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Join the ExecFlex Talent Network</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           max-width: 480px; margin: 60px auto; padding: 20px;
           background: #0B1120; color: #e2e8f0; }
    h1 { color: #ffffff; font-size: 24px; }
    p { color: #94a3b8; line-height: 1.6; }
    .card { background: #1E293B; border-radius: 12px;
            padding: 24px; margin: 24px 0; }
    input { width: 100%; padding: 12px; font-size: 16px;
            background: #0B1120; border: 1px solid #334155;
            border-radius: 8px; color: white; margin: 8px 0;
            box-sizing: border-box; }
    button { background: #5B6ABF; color: white; border: none;
             padding: 14px; font-size: 16px; border-radius: 8px;
             cursor: pointer; width: 100%; margin-top: 16px; }
    button:disabled { background: #475569; cursor: default; }
    .consent { font-size: 13px; color: #64748b; margin-top: 16px; }
    .questions { list-style: none; padding: 0; }
    .questions li { padding: 8px 0; border-bottom: 1px solid #334155;
                    color: #94a3b8; font-size: 14px; }
    .questions li:before { content: "\\2192  "; color: #5B6ABF; }
    .success { text-align: center; padding: 40px 20px; }
    .success h2 { color: #4ade80; }
  </style>
</head>
<body>
  <h1>Join the ExecFlex<br>Executive Talent Network</h1>
  <p>Ireland's first AI-powered executive talent network. Get matched
     to senior opportunities that fit you &mdash; without uploading a
     CV or filling in endless forms.</p>

  <div class="card">
    <h3 style="margin-top:0;color:#fff">How it works</h3>
    <p>Aidan &mdash; our AI consultant &mdash; will call you for a
       4-minute career conversation and ask:</p>
    <ul class="questions">
      <li>Are you open to new opportunities?</li>
      <li>What type of role interests you most?</li>
      <li>Which sectors are you passionate about?</li>
      <li>What salary or day rate are you targeting?</li>
      <li>What's your notice period?</li>
    </ul>
    <p style="font-size:13px;color:#64748b">
      This call is conducted by an AI. It takes 4 minutes. Your
      answers are used only to match you to relevant opportunities.
      You can request deletion of your data at any time by emailing
      compliance@ainm.ai.
    </p>
  </div>

  <div class="card" id="form-section">
    <h3 style="margin-top:0;color:#fff">Get started</h3>
    <input type="text" id="name" placeholder="Your full name" />
    <input type="email" id="email" placeholder="Work email address" />
    <input type="tel" id="phone"
           placeholder="Mobile number (e.g. +353 87 123 4567)" />

    <div class="consent">
      <label style="display:flex;gap:8px;align-items:flex-start">
        <input type="checkbox" id="consent" style="width:auto;margin-top:3px" />
        <span>I consent to being contacted by Aidan (an AI voice
        agent) for a 4-minute career conversation. I understand this
        call will be recorded and my responses used to match me to
        executive opportunities. I can withdraw consent at any time.</span>
      </label>
    </div>

    <button id="submit-btn" onclick="joinNetwork()">Request my call from Aidan &rarr;</button>
    <p id="error" style="color:#f87171;display:none;margin-top:12px;"></p>
  </div>

  <div class="success" id="success-section" style="display:none">
    <h2>You're in!</h2>
    <p>Aidan will call you within the next few minutes.<br>
       The call will come from an Irish number.<br>
       It takes about 4 minutes.</p>
    <p style="color:#64748b;font-size:13px">
      Check your email &mdash; we've sent you a confirmation with
      details of your rights under GDPR and the EU AI Act.
    </p>
  </div>

  <script>
  async function joinNetwork() {
    const name = document.getElementById('name').value.trim();
    const email = document.getElementById('email').value.trim();
    const phone = document.getElementById('phone').value.trim();
    const consent = document.getElementById('consent').checked;
    const err = document.getElementById('error');
    const btn = document.getElementById('submit-btn');

    if (!name || !email || !phone) {
      err.textContent = 'Please fill in all fields.';
      err.style.display = 'block';
      return;
    }
    if (!consent) {
      err.textContent = 'Please confirm your consent to proceed.';
      err.style.display = 'block';
      return;
    }
    err.style.display = 'none';
    btn.disabled = true;
    btn.textContent = 'Requesting call...';

    try {
      const r = await fetch('/talent-network/join', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          name, email, phone, consent: true,
          ref: new URLSearchParams(location.search).get('ref')
        })
      });
      const data = await r.json();
      if (r.ok) {
        document.getElementById('form-section').style.display = 'none';
        document.getElementById('success-section').style.display = 'block';
      } else {
        err.textContent = data.error || 'Something went wrong.';
        err.style.display = 'block';
        btn.disabled = false;
        btn.textContent = 'Request my call from Aidan \\u2192';
      }
    } catch (e) {
      err.textContent = 'Network error. Please try again.';
      err.style.display = 'block';
      btn.disabled = false;
      btn.textContent = 'Request my call from Aidan \\u2192';
    }
  }
  </script>
</body>
</html>"""


# ── GET / POST /talent-network/join ──────────────────────────────────────────

@talent_network_bp.route("/talent-network/join", methods=["GET"])
def talent_network_page():
    """Public landing page. No auth."""
    return Response(_LANDING_HTML, mimetype="text/html"), 200


@talent_network_bp.route("/talent-network/join", methods=["POST"])
def talent_network_submit():
    """
    Accept a candidate opt-in submission, upsert their profile,
    enqueue a talent_network Twilio call, and send a confirmation
    email. No auth — rate-limited per-IP and per-phone.
    """
    if not supabase_client:
        return jsonify({"error": "Service unavailable"}), 503

    ip = _get_client_ip()
    if not _check_ip_rate(ip):
        print(f"[TALENT-NET-OPTIN] Rate-limited ip={ip}", flush=True)
        return jsonify({"error": "Too many requests from this address. Please try again later."}), 429

    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    phone_raw = (data.get("phone") or "").strip()
    consent = bool(data.get("consent"))
    ref = (data.get("ref") or "").strip() or None

    # Validation
    if not name:
        return jsonify({"error": "name is required"}), 400
    if not _is_valid_email(email):
        return jsonify({"error": "a valid email address is required"}), 400
    phone = _normalise_phone(phone_raw)
    if not phone:
        return jsonify({"error": "a valid mobile number is required (e.g. +353 87 123 4567)"}), 400
    if not consent:
        return jsonify({"error": "You must consent to being contacted to proceed"}), 400

    # Per-phone rate limit — prevents someone repeatedly submitting the
    # same phone to rack up Twilio cost against one victim.
    if not _check_phone_rate(phone):
        print(f"[TALENT-NET-OPTIN] phone rate-limited phone={phone}", flush=True)
        return jsonify({"error": "This phone number already has a pending call. Please wait."}), 429

    now_iso = datetime.now(timezone.utc).isoformat()
    first_name, _, last_name = name.partition(" ")
    last_name = last_name.strip() or None
    first_name = first_name.strip() or None

    # Look for an existing people_profiles row by:
    #   1. source_metadata->>'upload_email' (how the upload/enrichment
    #      endpoints store candidate emails)
    #   2. channel_identities by (channel='email', value=email)
    existing_row = None
    try:
        sm_resp = (
            supabase_client.table("people_profiles")
            .select("id, first_name, last_name, headline, source_metadata, user_id, approved")
            .eq("source_metadata->>upload_email", email)
            .limit(1)
            .execute()
        )
        if sm_resp.data:
            existing_row = sm_resp.data[0]
    except Exception as e:
        print(f"[TALENT-NET-OPTIN] sm dedup lookup failed: {e}", flush=True)

    if not existing_row:
        try:
            ci_resp = (
                supabase_client.table("channel_identities")
                .select("user_id")
                .eq("channel", "email")
                .eq("value", email)
                .limit(1)
                .execute()
            )
            if ci_resp.data:
                linked_user_id = ci_resp.data[0].get("user_id")
                if linked_user_id:
                    pp_resp = (
                        supabase_client.table("people_profiles")
                        .select("id, first_name, last_name, headline, source_metadata, user_id, approved")
                        .eq("user_id", linked_user_id)
                        .limit(1)
                        .execute()
                    )
                    if pp_resp.data:
                        existing_row = pp_resp.data[0]
        except Exception as e:
            print(f"[TALENT-NET-OPTIN] ci dedup lookup failed: {e}", flush=True)

    sm_update = {
        "upload_email": email,
        "upload_phone": phone,
        "upload_source": "talent_network_optin",
        "upload_date": now_iso,
        "optin_date": now_iso,
        "optin_ref": ref,
        "optin_ip": ip,
        "consent_source": "talent_network_optin_page",
    }

    profile_id: Optional[str] = None
    try:
        if existing_row:
            merged_sm = dict(existing_row.get("source_metadata") or {})
            merged_sm.update(sm_update)
            update_payload: dict = {
                "source_metadata": merged_sm,
                "consent_given": True,
                "consent_given_at": now_iso,
            }
            # Fill-empty merge on name fields only
            if first_name and not existing_row.get("first_name"):
                update_payload["first_name"] = first_name
            if last_name and not existing_row.get("last_name"):
                update_payload["last_name"] = last_name
            supabase_client.table("people_profiles").update(update_payload).eq(
                "id", existing_row["id"]
            ).execute()
            profile_id = existing_row["id"]
            print(f"[TALENT-NET-OPTIN] Updated existing profile id={profile_id} email={email}", flush=True)
        else:
            insert_payload = {
                "first_name": first_name,
                "last_name": last_name,
                "approved": False,
                "source": "talent_network_optin",
                "source_metadata": sm_update,
                "consent_given": True,
                "consent_given_at": now_iso,
            }
            ins = supabase_client.table("people_profiles").insert(insert_payload).execute()
            if ins.data:
                profile_id = ins.data[0].get("id")
                print(f"[TALENT-NET-OPTIN] Created profile id={profile_id} email={email}", flush=True)
            else:
                print(f"[TALENT-NET-OPTIN] insert returned no data for email={email}", flush=True)
    except Exception as e:
        print(f"[TALENT-NET-OPTIN] profile upsert failed: {e}\n{traceback.format_exc()}", flush=True)
        return jsonify({"error": "Could not save your details. Please try again."}), 500

    # Enqueue the talent_network call. create_screening_job wires the
    # outbound_call_jobs row with the right call_type, the dispatcher
    # worker picks it up on its next poll and dials via Twilio.
    try:
        from services.screening_service import create_screening_job
        create_screening_job(
            candidate_phone=phone,
            candidate_name=name,
            role_title="your career",
            company_name="Ainm Search",
            questions=[],
            callback_url=None,
            source_candidate_id=profile_id,
            purpose="talent_network",
        )
    except Exception as e:
        print(f"[TALENT-NET-OPTIN] create_screening_job failed: {e}\n{traceback.format_exc()}", flush=True)
        return jsonify({"error": "Could not schedule your call. Please try again."}), 500

    # Send confirmation email (best-effort — never fails the request)
    try:
        from modules.email_sender import send_talent_network_confirmation
        send_talent_network_confirmation(
            candidate_email=email,
            candidate_name=name,
            phone=phone,
        )
    except Exception as e:
        print(f"[TALENT-NET-OPTIN] confirmation email failed: {e}", flush=True)

    # PostHog — track the opt-in (best-effort)
    try:
        from services.analytics_service import track
        track("talent_network_optin", profile_id, {
            "ref": ref,
            "has_email": True,
            "has_phone": True,
            "ip": ip,
        })
    except Exception as e:
        print(f"[TALENT-NET-OPTIN] analytics failed: {e}", flush=True)

    print(
        f"[TALENT-NET-OPTIN] SUCCESS profile_id={profile_id} email={email} "
        f"phone={phone} ref={ref!r} ip={ip}",
        flush=True,
    )

    return jsonify({
        "status": "queued",
        "message": "Aidan will call you shortly",
    }), 200
