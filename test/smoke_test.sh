#!/bin/bash

# ExecFlex API Smoke Test Suite
# Quick integration tests for all endpoints - checks for 200 status codes
# Designed for CI/CD pipeline validation
#
# Usage:
#   ./smoke_test.sh                    # Test against localhost:5001
#   ./smoke_test.sh http://localhost:5001  # Test against local server
#   ./smoke_test.sh https://execflex-backend-1.onrender.com  # Test production

set -e  # Exit on error for CI/CD

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Auto-detect API URL based on environment
# Priority: 1) Command line arg, 2) RENDER_SERVICE_URL, 3) Default to Render production, 4) localhost
if [ -n "$1" ]; then
    API_BASE="$1"
elif [ -n "$RENDER_SERVICE_URL" ]; then
    # Running in Render environment - use the service URL
    API_BASE="$RENDER_SERVICE_URL"
elif [ -n "$RENDER_EXTERNAL_URL" ]; then
    # Alternative Render env var
    API_BASE="$RENDER_EXTERNAL_URL"
else
    # Default to Render production URL
    API_BASE="https://execflex-backend-1.onrender.com"
fi

# Ensure URL starts with http:// or https://
if [[ ! "$API_BASE" =~ ^https?:// ]]; then
    echo -e "${YELLOW}Warning: URL missing protocol, assuming https://${NC}"
    API_BASE="https://${API_BASE}"
fi

# Remove trailing slash if present
API_BASE="${API_BASE%/}"

# Optional auth for protected endpoints (match, post-role, request-intro)
# Option A: Smoke-test bypass - set SMOKE_TEST_BYPASS_SECRET (must match backend env)
# Option B: Real JWT - set SMOKE_TEST_AUTH_TOKEN to a Bearer token
CURL_AUTH_HEADERS=""
if [ -n "$SMOKE_TEST_AUTH_TOKEN" ]; then
    CURL_AUTH_HEADERS="-H 'Authorization: Bearer ${SMOKE_TEST_AUTH_TOKEN}'"
elif [ -n "$SMOKE_TEST_BYPASS_SECRET" ]; then
    CURL_AUTH_HEADERS="-H 'X-Smoke-Test: ${SMOKE_TEST_BYPASS_SECRET}'"
fi

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}ExecFlex API Smoke Test Suite${NC}"
echo -e "${BLUE}Testing: ${API_BASE}${NC}"
if [ -n "$CURL_AUTH_HEADERS" ]; then
    echo -e "${BLUE}Auth: using SMOKE_TEST_* env (bypass or token)${NC}"
fi
echo -e "${BLUE}========================================${NC}"
echo ""

# Test counters
PASSED=0
FAILED=0
TOTAL=0

# Function to run a smoke test
# Usage: test_endpoint "name" METHOD "/path" ["body"] [expected_status] [use_auth] [content_type]
# use_auth: "auth" = add auth headers
# content_type: "form" = application/x-www-form-urlencoded (default for body is application/json)
test_endpoint() {
    local test_name="$1"
    local method="$2"
    local endpoint="$3"
    local data="$4"
    local expected_status="${5:-200}"
    local use_auth="${6:-false}"
    local content_type="${7:-json}"
    
    # When auth is required but no auth headers are set, accept 401 (endpoint exists and correctly rejects)
    if [ "$use_auth" = "auth" ] && [ -z "$CURL_AUTH_HEADERS" ]; then
        expected_status="200,201,401"
    fi
    
    ((TOTAL++))
    echo -e "${YELLOW}[${TOTAL}] Testing: ${test_name}${NC}"
    echo -e "  ${BLUE}${method} ${endpoint}${NC}"
    
    # Build curl command
    local curl_cmd="curl -s -w '\n%{http_code}' -X ${method}"
    
    if [ "$use_auth" = "auth" ] && [ -n "$CURL_AUTH_HEADERS" ]; then
        curl_cmd="${curl_cmd} ${CURL_AUTH_HEADERS}"
    fi
    
    if [ -n "$data" ]; then
        if [ "$content_type" = "form" ]; then
            curl_cmd="${curl_cmd} -H 'Content-Type: application/x-www-form-urlencoded' -d '${data}'"
        else
            curl_cmd="${curl_cmd} -H 'Content-Type: application/json' -d '${data}'"
        fi
    fi
    
    curl_cmd="${curl_cmd} '${API_BASE}${endpoint}'"
    
    # Execute and capture response
    local response=$(eval $curl_cmd 2>&1)
    local http_code=$(echo "$response" | tail -n1)
    local body=$(echo "$response" | sed '$d')
    
    # Check status code (expected_status can be "200" or "200,403" for multiple allowed)
    local ok=0
    if [[ "$expected_status" == *","* ]]; then
        IFS=',' read -ra statuses <<< "$expected_status"
        for s in "${statuses[@]}"; do
            if [ "$http_code" -eq "$s" ]; then ok=1; break; fi
        done
    else
        [ "$http_code" -eq "$expected_status" ] && ok=1
    fi
    if [ "$ok" -eq 1 ]; then
        echo -e "  ${GREEN}✓ PASS${NC} (HTTP ${http_code})"
        ((PASSED++))
    else
        echo -e "  ${RED}✗ FAIL${NC} (HTTP ${http_code}, expected ${expected_status})"
        echo -e "  ${RED}Response: ${body:0:200}${NC}"  # Truncate long responses
        ((FAILED++))
    fi
    echo ""
}

# ========================================
# Health Endpoints
# ========================================
test_endpoint "Health Check" "GET" "/" "" "200"

# ========================================
# Matching Endpoints (auth required)
# ========================================
test_endpoint "Find Match" "POST" "/match" \
    '{
        "industry": "Fintech",
        "expertise": "CFO",
        "availability": "fractional",
        "min_experience": 10,
        "max_salary": 150000,
        "location": "Ireland"
    }' "200" "auth"

# ========================================
# Roles Endpoints (auth required)
# ========================================
test_endpoint "Post Role" "POST" "/post-role" \
    '{
        "role_title": "Chief Financial Officer",
        "company_name": "Test Company",
        "industry": "Fintech",
        "role_description": "Leading financial strategy for Series B startup",
        "experience_level": "Senior",
        "commitment": "fractional",
        "role_type": "CFO",
        "is_remote": true,
        "location": "Ireland",
        "budget_range": "€100k-€150k",
        "contact_name": "Jane Doe",
        "contact_email": "jane@test.com",
        "phone": "+353123456789"
    }' "201" "auth"

# ========================================
# Introductions Endpoints (auth required)
# ========================================
test_endpoint "Request Introduction" "POST" "/request-intro" \
    '{
        "user_type": "client",
        "requester_name": "Jane Doe",
        "requester_email": "jane@test.com",
        "requester_company": "Test Corp",
        "match_id": "test-match-001",
        "notes": "Test introduction request"
    }' "200" "auth"

# ========================================
# Voice Endpoints (Twilio webhooks - no auth; 403 when signature missing in prod is OK)
# ========================================
test_endpoint "Voice Qualify (GET)" "GET" "/voice/qualify" "" "200,403"
test_endpoint "Voice Qualify (POST)" "POST" "/voice/qualify" "CallSid=CAtest&job_id=test-job" "200,403" "false" "form"
test_endpoint "Voice Inbound (GET)" "GET" "/voice/inbound" "" "200,403"
test_endpoint "Voice Status (GET)" "GET" "/voice/status" "" "200,403"

# ========================================
# Summary
# ========================================
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Smoke Test Summary${NC}"
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}Passed:  ${PASSED}/${TOTAL}${NC}"
echo -e "${RED}Failed:  ${FAILED}/${TOTAL}${NC}"
echo ""

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}✓ All smoke tests passed!${NC}"
    exit 0
else
    echo -e "${RED}✗ ${FAILED} smoke test(s) failed.${NC}"
    if [ -z "$CURL_AUTH_HEADERS" ]; then
        echo -e "${YELLOW}  For protected endpoints (match, post-role, request-intro) set SMOKE_TEST_BYPASS_SECRET or SMOKE_TEST_AUTH_TOKEN.${NC}"
        echo -e "${YELLOW}  On the server, set SMOKE_TEST_BYPASS_SECRET and SMOKE_TEST_USER_ID (a valid auth.users UUID).${NC}"
    fi
    exit 1
fi

