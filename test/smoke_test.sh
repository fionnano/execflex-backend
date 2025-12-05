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

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}ExecFlex API Smoke Test Suite${NC}"
echo -e "${BLUE}Testing: ${API_BASE}${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Test counters
PASSED=0
FAILED=0
TOTAL=0

# Function to run a smoke test
test_endpoint() {
    local test_name="$1"
    local method="$2"
    local endpoint="$3"
    local data="$4"
    local expected_status="${5:-200}"
    
    ((TOTAL++))
    echo -e "${YELLOW}[${TOTAL}] Testing: ${test_name}${NC}"
    echo -e "  ${BLUE}${method} ${endpoint}${NC}"
    
    # Build curl command
    local curl_cmd="curl -s -w '\n%{http_code}' -X ${method}"
    
    if [ -n "$data" ]; then
        curl_cmd="${curl_cmd} -H 'Content-Type: application/json' -d '${data}'"
    fi
    
    curl_cmd="${curl_cmd} '${API_BASE}${endpoint}'"
    
    # Execute and capture response
    local response=$(eval $curl_cmd 2>&1)
    local http_code=$(echo "$response" | tail -n1)
    local body=$(echo "$response" | sed '$d')
    
    # Check status code
    if [ "$http_code" -eq "$expected_status" ]; then
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
# Matching Endpoints
# ========================================
test_endpoint "Find Match" "POST" "/match" \
    '{
        "industry": "Fintech",
        "expertise": "CFO",
        "availability": "fractional",
        "min_experience": 10,
        "max_salary": 150000,
        "location": "Ireland"
    }' "200"

# ========================================
# Roles Endpoints
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
    }' "201"

# ========================================
# Introductions Endpoints
# ========================================
test_endpoint "Request Introduction" "POST" "/request-intro" \
    '{
        "user_type": "client",
        "requester_name": "Jane Doe",
        "requester_email": "jane@test.com",
        "requester_company": "Test Corp",
        "match_id": "test-match-001",
        "notes": "Test introduction request"
    }' "200"

# ========================================
# Voice Endpoints
# ========================================
test_endpoint "Call Candidate" "POST" "/call_candidate" \
    '{
        "phone": "+353123456789"
    }' "200"

test_endpoint "CORS Preflight - call_candidate" "OPTIONS" "/call_candidate" "" "200"

test_endpoint "Call Scheduling" "POST" "/call_scheduling" \
    '{
        "phone": "+353123456789",
        "executiveId": "test-exec-001",
        "executiveName": "Test Executive",
        "executiveExpertise": "CFO"
    }' "200"

test_endpoint "CORS Preflight - call_scheduling" "OPTIONS" "/call_scheduling" "" "200"

# Twilio webhook endpoints (GET and POST)
test_endpoint "Voice Intro (GET)" "GET" "/voice/intro" "" "200"

test_endpoint "Voice Intro (POST)" "POST" "/voice/intro" \
    "CallSid=CA1234567890abcdef" "200"

test_endpoint "Voice Capture (GET)" "GET" "/voice/capture?step=name" "" "200"

test_endpoint "Voice Capture (POST)" "POST" "/voice/capture?step=name" \
    "CallSid=CA1234567890abcdef&SpeechResult=test&Confidence=0.9" "200"

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
    exit 1
fi

