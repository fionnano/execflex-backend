# ExecFlex v1 Demo Script

**Duration:** ~10 minutes
**Prerequisites:** Backend running on localhost:5000, frontend on localhost:8080, both on rebuild-v1 branch.

---

## Setup

```bash
# Terminal 1 — Backend
cd execflex-backend
git checkout rebuild-v1
pip install -r requirements.txt
python server.py

# Terminal 2 — Frontend
cd execo-bridge
git checkout rebuild-v1
npm install
npm run dev
```

Note: The backend uses Supabase for database. Ensure `.env` has valid `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`. The frontend uses `VITE_FLASK_API_URL` (defaults to the Render deployment).

---

## Demo Path

### Step 1: Log In and Navigate to Agency Console

1. Open `http://localhost:8080/auth`
2. Log in with an existing account
3. Navigate to `http://localhost:8080/agency`
4. **Show:** Agency dashboard with stat cards (Active Jobs, Total Candidates, Pending Reviews, Pipeline)
5. **Point out:** Quick action buttons — Post New Job, View Pipeline, Compliance Centre

---

### Step 2: Create a Job (Pay Transparency Enforced)

1. Click "Post New Job" (or navigate to `/agency/jobs/new`)
2. Fill in the form:
   - Title: "Senior Software Engineer"
   - Description: "Build scalable fintech systems for Irish market"
   - Location: "Dublin, Ireland"
   - Commitment: Full-time
   - Industry: "Technology"
   - **Pay Range Min: 80000** (show: field is required)
   - **Pay Range Max: 120000**
   - Currency: EUR, Period: Annual
   - Remote: Yes
   - Skills: "Python, PostgreSQL, React"
   - Experience: 5-10 years
3. Submit
4. **Show:** Success toast. EU Pay Transparency Directive enforced — try removing pay range, see 400 error.
5. **Show:** Syndication panel appears — checkboxes for LinkedIn, Indeed, IrishJobs, Google Indexing

---

### Step 3: Syndicate to Job Boards

1. Check "LinkedIn" and "Indeed" checkboxes
2. Click "Syndicate"
3. **Show:** Success/fail per board (all succeed in mock mode)
4. **Point out:** This generates XML feeds — public at `/api/v1/syndication/feed/linkedin`
5. Navigate to `/agency/jobs` to see the job in the list

---

### Step 4: Create a Candidate

(Use API directly or navigate)

```bash
curl -X POST http://localhost:5000/api/v1/candidates \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "full_name": "Aoife Murphy",
    "email": "aoife@example.com",
    "phone": "+353851234567",
    "location": "Dublin, Ireland",
    "industry": "Technology",
    "skills": ["Python", "PostgreSQL", "React", "AWS"],
    "experience_years": 7,
    "compensation_min": 85000,
    "open_to": "active"
  }'
```

**Show:** Candidate created at pipeline stage "sourced"

---

### Step 5: Voice Screen (Stub Flow)

```bash
# Create screening session
curl -X POST http://localhost:5000/api/v1/screens \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"candidate_id": "<CANDIDATE_ID>", "opportunity_id": "<JOB_ID>"}'

# Give consent
curl -X POST http://localhost:5000/api/v1/screens/<SESSION_ID>/consent \
  -H "Authorization: Bearer $TOKEN"

# Answer questions (repeat for each question)
curl -X POST http://localhost:5000/api/v1/screens/<SESSION_ID>/answer \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"question_index": 0, "text": "I have 7 years of experience building Python microservices for fintech platforms, including payment processing and regulatory reporting systems."}'

# Score the session
curl -X POST http://localhost:5000/api/v1/screens/<SESSION_ID>/score \
  -H "Authorization: Bearer $TOKEN"
```

**Show:** Screening outcome (proceed/hold/reject), scores logged to ai_decision_log

---

### Step 6: Screening Summary → Review Queue

1. Navigate to `/agency/screening-review`
2. **Show:** The screening decision appears in the pending review queue
3. **Point out:** EU AI Act Art. 14 notice at top — "meaningful human oversight required"
4. Click "Approve" on the screening decision
5. **Show:** Decision moves to "Recently Reviewed" section

---

### Step 7: Run Matching

```bash
curl -X POST http://localhost:5000/api/v1/matches \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"job_id": "<JOB_ID>"}'
```

**Show:** Match results with:
- Composite score (0-100)
- Per-dimension breakdown (skills, industry, experience, location, availability, compensation, screening)
- Human-readable "why matched" summary
- Recommendation (proceed/hold/reject)

---

### Step 8: Pipeline Board — Human Review Gate

1. Navigate to `/agency/pipeline`
2. **Show:** Candidate in "sourced" column
3. Use the three-dot menu → "Move to screened"
4. **Show:** Candidate moves to screened column (toast confirms)
5. Move through stages: screened → shortlisted → interviewing
6. Now try "Move to rejected"
7. **Show:** Dialog appears requiring a reason (EU AI Act + GDPR Art. 22)
8. Type "Does not meet minimum experience requirement"
9. Click Confirm
10. **Show:** Candidate moves to rejected, reason logged to ai_decision_log + pipeline_events

---

### Step 9: Compliance Centre

1. Navigate to `/agency/compliance`
2. **AI Decisions tab:**
   - **Show:** All logged decisions (screening scores, match ranks, stage changes)
   - Filter by type, toggle "unreviewed only"
   - Click "Review" on any unreviewed decision
   - **Show:** Full explanation, Approve/Override options
3. **Data Rights tab:**
   - **Show:** GDPR request intake (public endpoint — candidates don't need an account)
   - Submit a test request via API:
     ```bash
     curl -X POST http://localhost:5000/api/v1/compliance/data-rights \
       -H "Content-Type: application/json" \
       -d '{"request_type": "access", "requester_name": "Test Candidate", "requester_email": "test@example.com", "details": "I would like to access all data you hold on me."}'
     ```
   - **Show:** Request appears in the Data Rights tab as "pending"
   - Process it: mark as "in_progress" then "completed"

---

### Step 10: AI Transparency Notice

1. Open `http://localhost:5000/api/v1/compliance/ai-notice`
2. **Show:** Public-facing AI notice (no auth required) covering:
   - Voice screening disclosure
   - Matching engine explanation
   - Scoring methodology
   - Candidate rights (GDPR Art. 15/17, right to explanation, human review)

---

## Key Points to Highlight

1. **Pay transparency enforced at API layer** — cannot create a job without pay range
2. **Human review gate** — no automated rejections without human + reason
3. **Full audit trail** — every AI decision logged with inputs, model, score, explanation
4. **Explainable matching** — per-dimension scores with human-readable reasons
5. **GDPR data rights** — public intake endpoint, no auth required for candidates
6. **Job syndication** — XML feeds for LinkedIn/Indeed/IrishJobs, adapter pattern for new boards
7. **EU AI Act compliance** — Art. 50 transparency notice, Art. 14 human oversight, Art. 12 logging
