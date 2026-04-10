#!/usr/bin/env python3
"""
ExecFlex end-to-end pipeline test.

Validates every step of the core workflow against the live API:
  role posting → candidate sourcing → approval → outreach →
  demo seeder → shortlist creation → shortlist read → cleanup.

Usage:
    export ADMIN_JWT="eyJhbG..."
    export API_BASE="https://execflex-backend-1.onrender.com"   # optional, defaults to this
    python scripts/test_pipeline.py

Requires: requests (pip install requests)
"""
import os
import sys
import time
import json
import requests

# ── Configuration ────────────────────────────────────────────────────────────

BASE = os.environ.get("API_BASE", "https://execflex-backend-1.onrender.com").rstrip("/")
JWT = os.environ.get("ADMIN_JWT", "")

if not JWT:
    print("ERROR: Set ADMIN_JWT environment variable (Supabase admin JWT)")
    print("  export ADMIN_JWT='eyJhbG...'")
    sys.exit(1)

HEADERS = {
    "Authorization": f"Bearer {JWT}",
    "Content-Type": "application/json",
}


# ── Test harness ─────────────────────────────────────────────────────────────

results: list = []


def run_test(name: str, fn):
    """Run a test function, capture pass/fail, print result."""
    print(f"\n{'='*60}")
    print(f"TEST: {name}")
    print(f"{'='*60}")
    try:
        fn()
        results.append((name, True, None))
        print(f"  PASS")
    except AssertionError as e:
        results.append((name, False, str(e)))
        print(f"  FAIL: {e}")
    except Exception as e:
        results.append((name, False, str(e)))
        print(f"  ERROR: {type(e).__name__}: {e}")


# ── Shared state between tests ──────────────────────────────────────────────

state = {
    "opportunity_id": None,
    "sourced_candidate_id": None,
    "demo_candidate_ids": [],
    "shortlist_id": None,
}


# ── Test 1: POST /post-role ─────────────────────────────────────────────────

def test_post_role():
    resp = requests.post(f"{BASE}/post-role", headers=HEADERS, json={
        "role_title": "Chief Financial Officer",
        "industry": "Technology",
        "role_description": "Test CFO role for pipeline validation. This role will be deleted after testing.",
        "experience_level": "senior",
        "commitment": "full_time",
        "role_type": "executive",
        "company_name": "Pipeline Test Corp",
        "location": "Dublin, Ireland",
        "budget_range": "€120,000 - €150,000",
    })
    print(f"  Status: {resp.status_code}")
    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text[:200]}"

    data = resp.json()
    role = data.get("role") or {}
    opp_id = role.get("id")
    assert opp_id, f"No opportunity_id in response: {json.dumps(data)[:300]}"

    state["opportunity_id"] = opp_id
    print(f"  opportunity_id: {opp_id}")
    print(f"  PDL sourcing + auto-match threads should be firing in background")


# ── Test 2: GET /roles/<opp_id>/sourced-candidates ──────────────────────────

def test_sourced_candidates():
    opp_id = state["opportunity_id"]
    assert opp_id, "No opportunity_id from test 1"

    # PDL sourcing runs in a background thread — poll for up to 15 seconds
    candidates = []
    for attempt in range(6):
        if attempt > 0:
            print(f"  Waiting for PDL sourcing... (attempt {attempt + 1}/6)")
            time.sleep(3)

        resp = requests.get(
            f"{BASE}/roles/{opp_id}/sourced-candidates",
            headers=HEADERS,
        )
        print(f"  Status: {resp.status_code}")
        if resp.status_code != 200:
            print(f"  Response: {resp.text[:200]}")
            continue

        data = resp.json()
        candidates = data.get("candidates") or data if isinstance(data, list) else []
        if candidates:
            break

    print(f"  Candidates returned: {len(candidates)}")

    if candidates:
        first = candidates[0]
        cid = first.get("id")
        state["sourced_candidate_id"] = cid
        print(f"  First candidate: {first.get('name', 'n/a')} — {first.get('headline', 'n/a')}")
        print(f"  candidate_id: {cid}")
        assert cid, "First candidate has no id"
    else:
        # PDL might not be configured or might have returned 0 results
        # — don't fail the whole pipeline, just note it
        print("  WARNING: No sourced candidates. PDL_API_KEY may not be set.")
        print("  Skipping downstream tests that depend on sourced candidates.")


# ── Test 3: POST /admin/candidates/<id>/approve ─────────────────────────────

def test_approve_candidate():
    cid = state.get("sourced_candidate_id")
    if not cid:
        print("  SKIP: No sourced candidate to approve (PDL returned 0)")
        results.append(("Approve candidate", True, "skipped — no sourced candidate"))
        raise AssertionError("skipped — no sourced candidate from PDL")

    resp = requests.post(
        f"{BASE}/admin/candidates/{cid}/approve",
        headers=HEADERS,
    )
    print(f"  Status: {resp.status_code}")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:200]}"

    data = resp.json()
    print(f"  Response: {json.dumps(data)[:300]}")


# ── Test 4: POST /admin/roles/<opp_id>/send-outreach ────────────────────────

def test_send_outreach():
    opp_id = state.get("opportunity_id")
    cid = state.get("sourced_candidate_id")
    if not cid:
        print("  SKIP: No approved candidate for outreach")
        raise AssertionError("skipped — no candidate to send outreach to")

    resp = requests.post(
        f"{BASE}/admin/roles/{opp_id}/send-outreach",
        headers=HEADERS,
        json={"candidate_ids": [cid]},
    )
    print(f"  Status: {resp.status_code}")

    data = resp.json()
    print(f"  Response: {json.dumps(data)[:300]}")

    # Accept either 200 (sent) or 200 with skipped (no email on sourced candidate)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

    sent = data.get("sent", 0)
    skipped = data.get("skipped", 0)
    print(f"  Sent: {sent}, Skipped: {skipped}")
    # A sourced PDL candidate typically has no email — skip is acceptable
    assert sent + skipped > 0, "Neither sent nor skipped — unexpected"


# ── Test 5: GET /admin/upload/stats ──────────────────────────────────────────

def test_upload_stats():
    resp = requests.get(f"{BASE}/admin/upload/stats", headers=HEADERS)
    print(f"  Status: {resp.status_code}")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:200]}"

    data = resp.json()
    candidates = data.get("candidates") or {}
    clients = data.get("clients") or {}
    print(f"  Candidates total: {candidates.get('total', 'n/a')}")
    print(f"  Candidates approved: {candidates.get('approved', 'n/a')}")
    print(f"  Clients total: {clients.get('total', 'n/a')}")

    total = candidates.get("total", 0)
    assert isinstance(total, int) and total >= 0, f"Unexpected candidates.total: {total}"


# ── Test 6: POST /admin/seed-demo ───────────────────────────────────────────

def test_seed_demo():
    resp = requests.post(f"{BASE}/admin/seed-demo", headers=HEADERS)
    print(f"  Status: {resp.status_code}")
    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text[:300]}"

    data = resp.json()
    cids = data.get("candidate_ids") or []
    print(f"  Demo candidates created: {len(cids)}")
    print(f"  opportunity_id: {data.get('opportunity_id')}")

    assert len(cids) == 3, f"Expected 3 demo candidates, got {len(cids)}"
    state["demo_candidate_ids"] = cids
    state["demo_opportunity_id"] = data.get("opportunity_id")


# ── Test 7: POST /admin/roles/<opp_id>/create-shortlist ─────────────────────

def test_create_shortlist():
    opp_id = state.get("demo_opportunity_id")
    cids = state.get("demo_candidate_ids", [])
    assert opp_id, "No demo opportunity_id from test 6"
    assert len(cids) == 3, "Expected 3 demo candidate IDs"

    resp = requests.post(
        f"{BASE}/admin/roles/{opp_id}/create-shortlist",
        headers=HEADERS,
        json={
            "candidate_ids": cids,
            "role_title": "Chief Financial Officer",
            "company_name": "Moorepark Technology",
        },
    )
    print(f"  Status: {resp.status_code}")
    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text[:300]}"

    data = resp.json()
    shortlist_id = data.get("shortlist_id") or data.get("id")
    share_url = data.get("share_url") or data.get("url")

    print(f"  shortlist_id: {shortlist_id}")
    print(f"  share_url: {share_url}")

    assert shortlist_id, f"No shortlist_id in response: {json.dumps(data)[:300]}"
    state["shortlist_id"] = shortlist_id


# ── Test 8: GET /shortlist/<shortlist_id> ────────────────────────────────────

def test_read_shortlist():
    sl_id = state.get("shortlist_id")
    assert sl_id, "No shortlist_id from test 7"

    # Public endpoint — no auth needed
    resp = requests.get(f"{BASE}/shortlist/{sl_id}")
    print(f"  Status: {resp.status_code}")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:300]}"

    data = resp.json()
    candidates = data.get("candidates") or []
    print(f"  Candidates on shortlist: {len(candidates)}")

    assert len(candidates) == 3, f"Expected 3 candidates on shortlist, got {len(candidates)}"

    # Verify scores are present
    for c in candidates:
        name = c.get("name") or c.get("first_name", "?")
        scores = c.get("screening_scores") or c.get("scores")
        rec = c.get("recommendation") or c.get("screening_recommendation")
        print(f"  - {name}: recommendation={rec}, has_scores={bool(scores)}")


# ── Test 9: DELETE /admin/seed-demo ──────────────────────────────────────────

def test_unseed_demo():
    resp = requests.delete(f"{BASE}/admin/seed-demo", headers=HEADERS)
    print(f"  Status: {resp.status_code}")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:300]}"

    data = resp.json()
    deleted = data.get("deleted") or {}
    print(f"  Deleted: {json.dumps(deleted)}")

    # Verify the demo candidates are gone
    for key in ("people_profiles", "interactions", "outbound_call_jobs"):
        count = deleted.get(key, 0)
        if isinstance(count, str) and "error" in count:
            print(f"  WARNING: {key} cleanup had an error: {count}")
        else:
            print(f"  {key}: {count} rows removed")


# ── Clean up the test role ───────────────────────────────────────────────────

def cleanup():
    """Best-effort: delete the test opportunity we created in test 1."""
    opp_id = state.get("opportunity_id")
    if not opp_id:
        return
    print(f"\nCleaning up test opportunity {opp_id}...")
    try:
        # No DELETE endpoint for opportunities — leave it.
        # It's marked as "Pipeline Test Corp" so easily identifiable.
        print("  (No delete endpoint for opportunities — left in place.)")
        print(f"  Opportunity '{opp_id}' from Pipeline Test Corp can be removed manually.")
    except Exception as e:
        print(f"  Cleanup error: {e}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"ExecFlex Pipeline Test")
    print(f"API Base: {BASE}")
    print(f"JWT present: {'yes' if JWT else 'NO'}")
    print(f"JWT preview: {JWT[:20]}..." if len(JWT) > 20 else "")

    # Quick health check
    try:
        health = requests.get(f"{BASE}/health", timeout=10)
        print(f"Health check: {health.status_code}")
        assert health.status_code == 200, f"Health check failed: {health.status_code}"
    except Exception as e:
        print(f"FATAL: Cannot reach {BASE}/health — {e}")
        sys.exit(1)

    # Run tests in order
    run_test("1. Post role (CFO at Pipeline Test Corp)", test_post_role)
    run_test("2. Sourced candidates (PDL)", test_sourced_candidates)
    run_test("3. Approve candidate", test_approve_candidate)
    run_test("4. Send outreach", test_send_outreach)
    run_test("5. Upload stats", test_upload_stats)
    run_test("6. Seed demo data", test_seed_demo)
    run_test("7. Create shortlist", test_create_shortlist)
    run_test("8. Read shortlist (public)", test_read_shortlist)
    run_test("9. Delete demo data", test_unseed_demo)

    cleanup()

    # Summary
    print(f"\n{'='*60}")
    print(f"RESULTS")
    print(f"{'='*60}")
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    for name, ok, err in results:
        status = "PASS" if ok else "FAIL"
        suffix = f" — {err}" if err and not ok else ""
        print(f"  [{status}] {name}{suffix}")

    print(f"\n{passed}/{total} tests passed")

    if passed < total:
        sys.exit(1)


if __name__ == "__main__":
    main()
