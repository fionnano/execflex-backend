"""
Email Introduction request routes.
"""
from datetime import datetime
from flask import request, Response
from routes import introductions_bp
from utils.response_helpers import ok, bad
from utils.auth_helpers import require_auth
from config.clients import supabase_client
from modules.email_sender import send_intro_email


@introductions_bp.route("/request-intro", methods=["POST"])
@require_auth
def request_intro():
    """
    Creates a thread and interaction for an intro request, sends email.
    Body (JSON):
      {
        "user_type": "client" | "candidate",
        "requester_name": "Jane Doe",
        "requester_email": "jane@acme.com",
        "requester_company": "Acme",
        "match_id": "cand-001",  # people_profiles.id or user_id
        "notes": "Series B GTM help",
        "opportunity_id": "optional-opp-id"
      }
    """
    try:
        # Tier quota check
        from services.billing_service import check_quota
        quota_user_id = request.environ.get("authenticated_user_id")
        allowed, quota_msg = check_quota(quota_user_id, "intros_made")
        if not allowed:
            return bad(quota_msg, 403, error_code="upgrade_required", upgrade_url="/pricing")

        data = request.get_json(force=True, silent=True) or {}
        required = ["user_type", "requester_name", "requester_email", "match_id"]
        missing = [f for f in required if not data.get(f)]
        if missing:
            return bad(f"Missing required fields: {', '.join(missing)}")

        # Get user_id from authenticated JWT token
        user_id = request.environ.get('authenticated_user_id')
        if not user_id:
            return bad("Authentication required", 401)

        # Resolve the requester's REAL name and company from the database.
        # The frontend currently passes the requester's email as
        # requester_name and the role title as requester_company — both
        # wrong. Look up the authoritative values here and override the
        # payload fields. Fall back to whatever the frontend sent if we
        # can't find a better source.
        resolved_requester_name = data.get("requester_name")
        resolved_requester_company = data.get("requester_company")
        try:
            req_profile = (
                supabase_client.table("people_profiles")
                .select("first_name, last_name")
                .eq("user_id", user_id)
                .limit(1)
                .execute()
            )
            if req_profile.data:
                pp = req_profile.data[0] or {}
                first = (pp.get("first_name") or "").strip()
                last = (pp.get("last_name") or "").strip()
                full_name = (f"{first} {last}").strip()
                if full_name:
                    resolved_requester_name = full_name
        except Exception as e:
            print(f"⚠️ Could not resolve requester name from people_profiles: {e}")

        # Company: prefer an organization row the user created themselves.
        # Fall back to the opportunity's organization if the caller passed
        # an opportunity_id (handled below after opportunity lookup).
        try:
            org_resp = (
                supabase_client.table("organizations")
                .select("name")
                .eq("created_by_user_id", user_id)
                .limit(1)
                .execute()
            )
            if org_resp.data and org_resp.data[0].get("name"):
                resolved_requester_company = org_resp.data[0]["name"]
        except Exception as e:
            print(f"⚠️ Could not resolve requester company from organizations: {e}")

        data["requester_name"] = resolved_requester_name or data.get("requester_name")
        data["requester_company"] = resolved_requester_company or data.get("requester_company")

        # Fetch candidate details from people_profiles
        candidate_name = "an executive"
        candidate_email = None
        candidate_user_id = None
        candidate_role = None
        candidate_industries = []
        
        try:
            # Try to fetch candidate details from people_profiles
            # match_id could be a profile id or user_id
            cand_response = supabase_client.table("people_profiles").select(
                "id, user_id, first_name, last_name, headline, industries"
            ).or_(f"id.eq.{data['match_id']},user_id.eq.{data['match_id']}").limit(1).execute()
            
            if cand_response.data and len(cand_response.data) > 0:
                cand = cand_response.data[0]
                first = cand.get("first_name") or ""
                last = cand.get("last_name") or ""
                candidate_name = " ".join([p for p in [first, last] if p]).strip() or "an executive"
                candidate_user_id = cand.get("user_id")
                candidate_role = cand.get("headline") or None
                candidate_industries = cand.get("industries") or []
                
                # Try to get email from channel_identities
                if candidate_user_id:
                    email_response = supabase_client.table("channel_identities").select("value").eq("user_id", candidate_user_id).eq("channel", "email").limit(1).execute()
                    if email_response.data and len(email_response.data) > 0:
                        candidate_email = email_response.data[0].get("value")
        except Exception as e:
            print(f"⚠️ Could not fetch candidate details: {e}")

        # Get opportunity_id if provided + fetch full opportunity details
        # for the outreach-email generation prompt.
        opportunity_id = data.get("opportunity_id")
        opportunity_record: dict = {}
        if opportunity_id:
            try:
                opp_resp = (
                    supabase_client.table("opportunities")
                    .select("id, title, description, location, compensation, industry, organization_id, metadata")
                    .eq("id", opportunity_id)
                    .limit(1)
                    .execute()
                )
                if opp_resp.data:
                    opportunity_record = opp_resp.data[0] or {}
                    # Hydrate company name from organizations table if possible
                    org_id = opportunity_record.get("organization_id")
                    if org_id:
                        try:
                            org_resp = (
                                supabase_client.table("organizations")
                                .select("name")
                                .eq("id", org_id)
                                .limit(1)
                                .execute()
                            )
                            if org_resp.data:
                                opp_org_name = org_resp.data[0].get("name")
                                opportunity_record["company_name"] = opp_org_name
                                # Secondary fallback: if we still don't have
                                # a requester_company, use the opportunity's
                                # org name (only happens when the hirer
                                # didn't create the org row themselves).
                                if opp_org_name and not resolved_requester_company:
                                    data["requester_company"] = opp_org_name
                        except Exception as e:
                            print(f"⚠️ Could not fetch organisation name: {e}")
            except Exception as e:
                print(f"⚠️ Could not fetch opportunity record: {e}")

        # Create or find thread for this intro
        thread_id = None
        try:
            # Try to find existing thread for this user and candidate
            thread_subject = f"Introduction: {data['requester_name']} ↔ {candidate_name}"
            if opportunity_id:
                thread_subject = f"Opportunity Match: {candidate_name}"
            
            # Create new thread
            thread_payload = {
                "primary_user_id": user_id,
                "subject": thread_subject,
                "status": "open",
                "opportunity_id": opportunity_id,
                "active": True
            }
            thread_response = supabase_client.table("threads").insert(thread_payload).execute()
            if thread_response.data and len(thread_response.data) > 0:
                thread_id = thread_response.data[0].get("id")
        except Exception as e:
            print(f"⚠️ Could not create thread: {e}")
            return bad(f"Failed to create thread: {str(e)}", 500)

        if not thread_id:
            return bad("Failed to create thread", 500)

        # Send introduction email if we have candidate email
        email_sent = False
        interaction_id = None
        
        if candidate_email:
            # Generate LLM outreach email (falls back to static template on failure)
            try:
                from services.outreach_service import generate_outreach_email, append_response_links
                candidate_profile = {
                    "name": candidate_name,
                    "headline": candidate_role,
                    "years_experience": None,
                    "industries": candidate_industries,
                }
                outreach = generate_outreach_email(candidate_profile, opportunity_record)
                outreach_subject = outreach.get("subject")
                outreach_body = outreach.get("body") or ""
                # Append interested / not-interested response links
                outreach_body_with_links = append_response_links(outreach_body, thread_id)
            except Exception as e:
                print(f"⚠️ Outreach generation failed, falling back to template: {e}")
                outreach_subject = None
                outreach_body = ""
                outreach_body_with_links = ""

            try:
                email_sent = send_intro_email(
                    client_name=data["requester_name"],
                    client_email=data["requester_email"],
                    candidate_name=candidate_name,
                    candidate_email=candidate_email,
                    subject=outreach_subject,
                    candidate_role=candidate_role,
                    candidate_industries=candidate_industries if isinstance(candidate_industries, list) else [],
                    requester_company=data.get("requester_company"),
                    user_type=data["user_type"],
                    match_id=data["match_id"],
                    body_extra=data.get("notes"),
                    thread_id=thread_id,
                    plain_body_override=outreach_body_with_links or None,
                )

                # Create interaction record for the email
                try:
                    interaction_payload = {
                        "thread_id": thread_id,
                        "user_id": user_id,
                        "channel": "email",
                        "direction": "outbound",
                        "provider": "gmail",
                        "started_at": datetime.utcnow().isoformat() + "Z",
                        "ended_at": datetime.utcnow().isoformat() + "Z",
                        "summary_text": f"Introduction email sent from {data['requester_name']} ({data['requester_email']}) to {candidate_name} ({candidate_email})",
                        "artifacts": {
                            "recipient_email": data["requester_email"],
                            "candidate_email": candidate_email,
                            "candidate_name": candidate_name,
                            "status": "sent" if email_sent else "failed",
                            "notes": data.get("notes"),
                            "outreach_email_subject": outreach_subject,
                            "outreach_email_body": outreach_body_with_links or outreach_body,
                        }
                    }
                    interaction_response = supabase_client.table("interactions").insert(interaction_payload).execute()
                    if interaction_response.data and len(interaction_response.data) > 0:
                        interaction_id = interaction_response.data[0].get("id")
                except Exception as e:
                    print(f"⚠️ Could not create interaction record: {e}")
                
                # Update thread status based on email result
                try:
                    new_status = "waiting_on_user" if email_sent else "open"
                    supabase_client.table("threads").update({"status": new_status}).eq("id", thread_id).execute()
                except Exception as e:
                    print(f"⚠️ Could not update thread status: {e}")
                    
            except Exception as e:
                print(f"⚠️ Error sending intro email: {e}")
        else:
            print(f"⚠️ No candidate email found for match_id {data['match_id']}, email not sent")

        payload = {
            "thread_id": thread_id,
            "interaction_id": interaction_id,
            "email_sent": email_sent,
            "status": "sent" if email_sent else "pending"
        }
        return ok(payload)

    except Exception as e:
        print("❌ /request-intro error:", e)
        return bad(str(e), 500)


# ── Candidate response endpoint ──────────────────────────────────────────────

_RESPONSE_PAGE_INTERESTED = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Thanks — Ainm Search</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            max-width: 560px; margin: 80px auto; padding: 0 24px; color: #1a1a1a; }}
    h1 {{ font-size: 28px; font-weight: 600; }}
    p  {{ font-size: 17px; line-height: 1.55; color: #444; }}
    .box {{ background: #f5f5f7; border-radius: 12px; padding: 24px; margin-top: 24px; }}
  </style>
</head>
<body>
  <h1>Brilliant — we'll be in touch.</h1>
  <p>Thanks for letting us know you're interested. A member of the Ainm Search team will
  reach out within one working day to tell you more about the role and arrange a short call.</p>
  <div class="box">
    <p style="margin:0;"><strong>What happens next?</strong><br>
    You'll get a short call from AI Dan, our voice assistant, to gather a few details
    about your background. It takes about 5 minutes and helps us match you properly.</p>
  </div>
</body>
</html>"""

_RESPONSE_PAGE_NOT_INTERESTED = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Thanks — Ainm Search</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            max-width: 560px; margin: 80px auto; padding: 0 24px; color: #1a1a1a; }}
    h1 {{ font-size: 28px; font-weight: 600; }}
    p  {{ font-size: 17px; line-height: 1.55; color: #444; }}
  </style>
</head>
<body>
  <h1>No problem — thanks for letting us know.</h1>
  <p>We've marked this one as "not right now" and we won't be in touch about it again.
  If your circumstances change you can always reach us at hello@ainm.ai.</p>
  <p>All the best,<br>The Ainm Search team</p>
</body>
</html>"""

_RESPONSE_PAGE_ERROR = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Ainm Search</title></head>
<body style="font-family: sans-serif; max-width:560px; margin:80px auto; padding:0 24px;">
  <h1>Something went wrong</h1>
  <p>We couldn't record your response. Please reply to the original email or contact
  hello@ainm.ai and we'll sort it out.</p>
</body>
</html>"""


@introductions_bp.route("/intro/respond", methods=["GET"])
def intro_respond():
    """
    GET /intro/respond?thread_id=<uuid>&action=interested|notinterested

    Candidate self-service endpoint from the outreach email links.
    No auth — the thread_id in the link acts as the credential.

    Interested:
      - Sets threads.status = 'candidate_interested'
      - Looks up the candidate's phone from channel_identities
      - Enqueues an internal /screening call
      - Returns an HTML confirmation page

    Not interested:
      - Sets threads.status = 'candidate_declined'
      - Returns an HTML thank-you page
    """
    thread_id = (request.args.get("thread_id") or "").strip()
    action = (request.args.get("action") or "").strip().lower()

    if not thread_id or action not in ("interested", "notinterested"):
        return Response(_RESPONSE_PAGE_ERROR, mimetype="text/html"), 400

    if not supabase_client:
        return Response(_RESPONSE_PAGE_ERROR, mimetype="text/html"), 503

    try:
        # Load the thread
        thread_resp = (
            supabase_client.table("threads")
            .select("id, primary_user_id, opportunity_id, subject")
            .eq("id", thread_id)
            .limit(1)
            .execute()
        )
        if not thread_resp.data:
            print(f"[INTRO RESPOND] thread_id not found: {thread_id}", flush=True)
            return Response(_RESPONSE_PAGE_ERROR, mimetype="text/html"), 404
        thread = thread_resp.data[0]

        new_status = "candidate_interested" if action == "interested" else "candidate_declined"
        try:
            supabase_client.table("threads").update({"status": new_status}).eq("id", thread_id).execute()
        except Exception as e:
            print(f"[INTRO RESPOND] thread update failed: {e}", flush=True)

        print(
            f"[INTRO RESPOND] thread={thread_id} action={action} status={new_status}",
            flush=True,
        )

        if action == "notinterested":
            return Response(_RESPONSE_PAGE_NOT_INTERESTED, mimetype="text/html"), 200

        # Action = interested → look up candidate phone + enqueue screening call
        try:
            candidate_user_id = thread.get("primary_user_id")
            phone = None
            if candidate_user_id:
                ci_resp = (
                    supabase_client.table("channel_identities")
                    .select("value")
                    .eq("user_id", candidate_user_id)
                    .eq("channel", "phone")
                    .limit(1)
                    .execute()
                )
                if ci_resp.data:
                    phone = ci_resp.data[0].get("value")

            if not phone:
                print(
                    f"[INTRO RESPOND] interested but no phone for user={candidate_user_id} "
                    f"thread={thread_id} — skipping auto-screening",
                    flush=True,
                )
            else:
                # Fetch opportunity context for the screening call
                opp_id = thread.get("opportunity_id")
                role_title = "Executive Role"
                company_name = "Hiring Company"
                if opp_id:
                    try:
                        opp_resp = (
                            supabase_client.table("opportunities")
                            .select("title, organization_id")
                            .eq("id", opp_id)
                            .limit(1)
                            .execute()
                        )
                        if opp_resp.data:
                            opp = opp_resp.data[0]
                            role_title = opp.get("title") or role_title
                            org_id = opp.get("organization_id")
                            if org_id:
                                org_resp = (
                                    supabase_client.table("organizations")
                                    .select("name")
                                    .eq("id", org_id)
                                    .limit(1)
                                    .execute()
                                )
                                if org_resp.data:
                                    company_name = org_resp.data[0].get("name") or company_name
                    except Exception as e:
                        print(f"[INTRO RESPOND] opp fetch failed: {e}", flush=True)

                # Fetch candidate name (best effort)
                candidate_name = "Candidate"
                try:
                    if candidate_user_id:
                        pp_resp = (
                            supabase_client.table("people_profiles")
                            .select("first_name, last_name")
                            .eq("user_id", candidate_user_id)
                            .limit(1)
                            .execute()
                        )
                        if pp_resp.data:
                            pp = pp_resp.data[0]
                            first = pp.get("first_name") or ""
                            last = pp.get("last_name") or ""
                            candidate_name = (f"{first} {last}").strip() or candidate_name
                except Exception as e:
                    print(f"[INTRO RESPOND] profile fetch failed: {e}", flush=True)

                # Enqueue screening job directly via the service layer — this
                # avoids making an HTTP call back to ourselves (which would
                # need auth) and keeps everything in-process.
                try:
                    from services.screening_service import create_screening_job
                    default_questions = [
                        {"question": "Tell me about your current role and what you're looking for next.", "competency": "motivation", "weight": 1.0},
                        {"question": "What would you say are your two or three biggest strengths?", "competency": "self_awareness", "weight": 1.0},
                        {"question": "What kind of salary range and location are you considering?", "competency": "practical_fit", "weight": 1.0},
                    ]
                    create_screening_job(
                        candidate_phone=phone,
                        candidate_name=candidate_name,
                        role_title=role_title,
                        company_name=company_name,
                        questions=default_questions,
                        callback_url=None,
                        source_candidate_id=candidate_user_id,
                        purpose="candidate_chat",
                        role_id=opp_id,
                    )
                    print(
                        f"[INTRO RESPOND] screening enqueued: thread={thread_id} "
                        f"phone={phone} role={role_title!r}",
                        flush=True,
                    )
                except Exception as e:
                    print(f"[INTRO RESPOND] create_screening_job failed: {e}", flush=True)
        except Exception as e:
            print(f"[INTRO RESPOND] interested-flow error: {e}", flush=True)
            # still show the happy page — the thread is marked interested

        return Response(_RESPONSE_PAGE_INTERESTED, mimetype="text/html"), 200
    except Exception as e:
        print(f"[INTRO RESPOND] top-level error: {e}", flush=True)
        return Response(_RESPONSE_PAGE_ERROR, mimetype="text/html"), 500
