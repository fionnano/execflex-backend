# ExecFlex API Regression Test Suite

A comprehensive curl-based test suite to verify all API endpoints are working correctly after deployment to `api.execflex.ai`.

## Quick Start

```bash
# Navigate to the test directory
cd backend/test

# Test against production (api.execflex.ai) - defaults to https://
./test_api.sh

# Test against local server (use http:// for local)
./test_api.sh http://localhost:5001

# Test against custom URL (always use https:// for production)
./test_api.sh https://api.execflex.ai
```

**Important:** Always use `https://` for production URLs to avoid 307 redirects. The script defaults to `https://api.execflex.ai`.

Or run from the backend directory:

```bash
# From backend directory
./test/test_api.sh
```

## Requirements

- `curl` (standard on macOS/Linux)
- `bash` (standard on macOS/Linux)
- `jq` (optional, for pretty JSON output)

## What It Tests

The test suite covers all major API endpoints:

### Health & Status
- ✅ `GET /` - Health check

### Role Management
- ✅ `GET /view-roles` - List all role postings
- ✅ `POST /post-role` - Create new role posting
- ✅ `POST /post-role` - Validation (missing fields → 400)

### Executive Matching
- ✅ `POST /match` - Find best candidate match
- ✅ `POST /match` - Validation (missing fields → 400)

### Introductions
- ✅ `POST /request-intro` - Request introduction (recommended)
- ✅ `POST /request-intro` - Validation (missing fields → 400)

### Feedback
- ✅ `POST /feedback` - Submit feedback
- ✅ `POST /feedback` - Alternative field names
- ✅ `POST /feedback` - Validation (missing fields → 400)

### Voice/Telephony
- ✅ `POST /call_candidate` - Initiate outbound call (may skip if Twilio not configured)
- ✅ `POST /call_candidate` - Validation (missing phone → 400)
- ✅ `OPTIONS /call_candidate` - CORS preflight

## Test Output

The script provides color-coded output:
- 🟢 **Green** - Test passed
- 🔴 **Red** - Test failed
- 🟡 **Yellow** - Test skipped (non-critical, e.g., Twilio not configured)

At the end, you'll see a summary:
```
========================================
Test Summary
========================================
Passed:  14
Failed:  0
Skipped: 2
```

## Exit Codes

- `0` - All critical tests passed
- `1` - One or more tests failed

This makes it easy to integrate into CI/CD pipelines:

```bash
./test_api.sh && echo "Deployment verified!" || echo "Deployment failed!"
```

## Manual Testing

If you prefer to test endpoints manually, here are example curl commands:

### Health Check
```bash
curl https://api.execflex.ai/
```

### View Roles
```bash
curl https://api.execflex.ai/view-roles
```

### Find Match
```bash
curl -X POST https://api.execflex.ai/match \
  -H "Content-Type: application/json" \
  -d '{
    "industry": "Fintech",
    "expertise": "CFO",
    "availability": "fractional",
    "min_experience": "10",
    "max_salary": "150000",
    "location": "Ireland"
  }'
```

### Post Role
```bash
curl -X POST https://api.execflex.ai/post-role \
  -H "Content-Type: application/json" \
  -d '{
    "role_title": "Chief Financial Officer",
    "company_name": "Test Company",
    "industry": "Fintech",
    "role_description": "Leading financial strategy",
    "experience_level": "Senior",
    "commitment": "fractional",
    "role_type": "CFO",
    "is_remote": true,
    "location": "Ireland"
  }'
```

### Request Introduction
```bash
curl -X POST https://api.execflex.ai/request-intro \
  -H "Content-Type: application/json" \
  -d '{
    "user_type": "client",
    "requester_name": "Jane Doe",
    "requester_email": "jane@test.com",
    "match_id": "test-match-001"
  }'
```

### Submit Feedback
```bash
curl -X POST https://api.execflex.ai/feedback \
  -H "Content-Type: application/json" \
  -d '{
    "user_name": "Jane Doe",
    "match_name": "John Smith",
    "feedback_text": "Great match!"
  }'
```

## Troubleshooting

### Tests Fail with Connection Errors
- Verify the API is deployed and accessible
- Check firewall/network settings
- Verify the URL is correct (no trailing slash)

### Email/Intro Tests Fail
- These may fail if email service (Gmail SMTP) is not configured
- Check environment variables: `EMAIL_USER`, `EMAIL_PASS`

### Voice/Call Tests Fail
- These may fail if Twilio is not configured
- Check environment variables: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`
- These tests are marked as "skip on error" and won't fail the suite

### JSON Parsing Errors
- Install `jq` for better JSON output: `brew install jq` (macOS) or `apt-get install jq` (Linux)
- The script works without `jq`, but JSON output won't be pretty-printed

## Integration with CI/CD

Example GitHub Actions workflow:

```yaml
name: API Regression Tests

on:
  push:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Run API Tests
        run: |
          cd backend/test
          chmod +x test_api.sh
          ./test_api.sh https://api.execflex.ai
```

## Notes

- The test suite uses realistic but test data
- Some endpoints may create records in Supabase (roles, feedback, intros)
- The suite is designed to be idempotent - you can run it multiple times
- Failed tests are clearly marked and include the HTTP status code and response body

