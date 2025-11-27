#!/bin/bash

# ExecFlex API Regression Test Suite
# Tests all API endpoints to verify deployment to api.execflex.ai
#
# Usage:
#   ./test_api.sh                    # Test against api.execflex.ai
#   ./test_api.sh http://localhost:5001  # Test against local server
#   ./test_api.sh https://api.execflex.ai  # Explicitly test production

# Don't exit on error - we want to run all tests
set +e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# API base URL (default to https://api.execflex.ai)
# Note: Always use https:// to avoid 307 redirects
API_BASE="${1:-https://api.execflex.ai}"

# Ensure URL starts with http:// or https://
if [[ ! "$API_BASE" =~ ^https?:// ]]; then
    echo -e "${YELLOW}Warning: URL missing protocol, assuming https://${NC}"
    API_BASE="https://${API_BASE}"
fi

# Remove trailing slash if present
API_BASE="${API_BASE%/}"

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}ExecFlex API Regression Test Suite${NC}"
echo -e "${BLUE}Testing: ${API_BASE}${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# Test counters
PASSED=0
FAILED=0
SKIPPED=0

# Test result tracking
declare -a TEST_RESULTS

# Function to run a test
run_test() {
    local test_name="$1"
    local method="$2"
    local endpoint="$3"
    local data="$4"
    local expected_status="${5:-200}"
    local skip_on_error="${6:-false}"
    
    echo -e "${YELLOW}Testing: ${test_name}${NC}"
    echo -e "  ${BLUE}${method} ${endpoint}${NC}"
    
    if [ -n "$data" ]; then
        echo -e "  ${BLUE}Payload: ${data}${NC}"
    fi
    
    # Build curl command
    # -L: Follow redirects (handles 301, 302, 307, 308)
    # -s: Silent mode (but keep errors)
    # -w: Write HTTP status code to stdout
    # --max-redirs 5: Limit redirects to prevent loops
    # --location-trusted: Trust redirects (for HTTPS redirects)
    local curl_cmd="curl -s -L --max-redirs 5 --location-trusted -w '\n%{http_code}' -X ${method}"
    
    if [ -n "$data" ]; then
        curl_cmd="${curl_cmd} -H 'Content-Type: application/json' -d '${data}'"
    fi
    
    curl_cmd="${curl_cmd} '${API_BASE}${endpoint}'"
    
    # Execute and capture response
    local response=$(eval $curl_cmd 2>&1)
    local http_code=$(echo "$response" | tail -n1)
    local body=$(echo "$response" | sed '$d')
    
    # Handle redirects (307, 308) - show what's happening
    if [ "$http_code" = "307" ] || [ "$http_code" = "308" ]; then
        echo -e "  ${YELLOW}⚠ WARNING${NC} (HTTP ${http_code} - Redirect detected)"
        echo -e "  ${YELLOW}Note: curl should follow redirects with -L flag. If you see this, check the redirect target.${NC}"
        # Try to get the redirect location
        local location=$(echo "$body" | grep -i "location:" | head -1 || echo "")
        if [ -n "$location" ]; then
            echo -e "  ${BLUE}Redirect to: ${location}${NC}"
        fi
    fi
    
    # Handle connection errors (curl returns 000 on connection failure)
    if [ "$http_code" = "000" ] || [ -z "$http_code" ]; then
        if [ "$skip_on_error" = "true" ]; then
            echo -e "  ${YELLOW}⊘ SKIP${NC} (Connection failed - skipping on error)"
            ((SKIPPED++))
            TEST_RESULTS+=("SKIP: ${method} ${endpoint} - ${test_name} (Connection failed)")
        else
            echo -e "  ${RED}✗ FAIL${NC} (Connection failed)"
            ((FAILED++))
            TEST_RESULTS+=("FAIL: ${method} ${endpoint} - ${test_name} (Connection failed)")
            echo -e "  ${RED}Error: ${body}${NC}"
        fi
        echo ""
        return
    fi
    
    # Check status code
    if [ "$http_code" -eq "$expected_status" ]; then
        echo -e "  ${GREEN}✓ PASS${NC} (HTTP ${http_code})"
        ((PASSED++))
        TEST_RESULTS+=("PASS: ${method} ${endpoint} - ${test_name}")
        
        # Pretty print JSON response if possible
        if command -v jq &> /dev/null && echo "$body" | jq . &> /dev/null; then
            echo -e "  ${BLUE}Response:${NC}"
            echo "$body" | jq . | sed 's/^/    /'
        else
            echo -e "  ${BLUE}Response: ${body}${NC}"
        fi
    else
        if [ "$skip_on_error" = "true" ]; then
            echo -e "  ${YELLOW}⊘ SKIP${NC} (HTTP ${http_code} - expected ${expected_status}, but skipping on error)"
            ((SKIPPED++))
            TEST_RESULTS+=("SKIP: ${method} ${endpoint} - ${test_name} (HTTP ${http_code})")
        else
            echo -e "  ${RED}✗ FAIL${NC} (HTTP ${http_code}, expected ${expected_status})"
            ((FAILED++))
            TEST_RESULTS+=("FAIL: ${method} ${endpoint} - ${test_name} (HTTP ${http_code})")
            echo -e "  ${RED}Response: ${body}${NC}"
        fi
    fi
    echo ""
}

# Test 1: Health Check
run_test "Health Check (GET /)" "GET" "/" "" "200"

# Test 2: View Roles (should work even if empty)
run_test "View Roles (GET /view-roles)" "GET" "/view-roles" "" "200"

# Test 3: Match - Valid request
run_test "Find Match - Valid Request" "POST" "/match" \
    '{
        "industry": "Fintech",
        "expertise": "CFO",
        "availability": "fractional",
        "min_experience": "10",
        "max_salary": "150000",
        "location": "Ireland"
    }' "200"

# Test 4: Match - Missing required fields (should return 400)
run_test "Find Match - Missing Fields (400)" "POST" "/match" \
    '{
        "industry": "Fintech"
    }' "400"

# Test 5: Post Role - Valid request
run_test "Post Role - Valid Request" "POST" "/post-role" \
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

# Test 6: Post Role - Missing required fields (should return 400)
run_test "Post Role - Missing Fields (400)" "POST" "/post-role" \
    '{
        "role_title": "CFO"
    }' "400"

# Test 7: Request Introduction - Valid request
run_test "Request Introduction - Valid Request" "POST" "/request-intro" \
    '{
        "user_type": "client",
        "requester_name": "Jane Doe",
        "requester_email": "jane@test.com",
        "requester_company": "Test Corp",
        "match_id": "test-match-001",
        "notes": "Series B GTM help needed"
    }' "200"

# Test 8: Request Introduction - Missing required fields (should return 400)
run_test "Request Introduction - Missing Fields (400)" "POST" "/request-intro" \
    '{
        "user_type": "client"
    }' "400"

# Test 9: Send Intro (Legacy) - Valid request
run_test "Send Intro (Legacy) - Valid Request" "POST" "/send_intro" \
    '{
        "client_name": "Jane Doe",
        "match_name": "John Smith",
        "email": "jane@test.com",
        "candidate_email": "john@test.com",
        "user_type": "client"
    }' "200" "true"  # Skip on error since email sending may fail

# Test 10: Send Intro (Legacy) - Missing required fields (should return 400)
run_test "Send Intro (Legacy) - Missing Fields (400)" "POST" "/send_intro" \
    '{
        "client_name": "Jane Doe"
    }' "400"

# Test 11: Submit Feedback - Valid request
run_test "Submit Feedback - Valid Request" "POST" "/feedback" \
    '{
        "user_name": "Jane Doe",
        "match_name": "John Smith",
        "feedback_text": "Great match! Very responsive and professional."
    }' "200"

# Test 12: Submit Feedback - Alternative field names
run_test "Submit Feedback - Alternative Fields" "POST" "/feedback" \
    '{
        "user": "Jane Doe",
        "match": "John Smith",
        "feedback": "Excellent service!"
    }' "200"

# Test 13: Submit Feedback - Missing required fields (should return 400)
run_test "Submit Feedback - Missing Fields (400)" "POST" "/feedback" \
    '{
        "user_name": "Jane Doe"
    }' "400"

# Test 14: Call Candidate - Valid request (may fail if Twilio not configured)
run_test "Call Candidate - Valid Request" "POST" "/call_candidate" \
    '{
        "phone": "+353123456789"
    }' "200" "true"  # Skip on error since Twilio may not be configured

# Test 15: Call Candidate - Missing phone (should return 400)
run_test "Call Candidate - Missing Phone (400)" "POST" "/call_candidate" \
    '{}' "400"

# Test 16: CORS Preflight for call_candidate
run_test "CORS Preflight - call_candidate" "OPTIONS" "/call_candidate" "" "200"

# Summary
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Test Summary${NC}"
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}Passed:  ${PASSED}${NC}"
echo -e "${RED}Failed:  ${FAILED}${NC}"
echo -e "${YELLOW}Skipped: ${SKIPPED}${NC}"
echo ""

# Always show detailed endpoint results
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}Endpoint Test Results${NC}"
echo -e "${BLUE}========================================${NC}"
if [ ${#TEST_RESULTS[@]} -eq 0 ]; then
    echo -e "${YELLOW}No test results recorded${NC}"
else
    for result in "${TEST_RESULTS[@]}"; do
        if [[ $result == PASS:* ]]; then
            echo -e "  ${GREEN}✓${NC} ${result#PASS: }"
        elif [[ $result == SKIP:* ]]; then
            echo -e "  ${YELLOW}⊘${NC} ${result#SKIP: }"
        else
            echo -e "  ${RED}✗${NC} ${result#FAIL: }"
        fi
    done
fi
echo ""

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}✓ All critical tests passed!${NC}"
    if [ $SKIPPED -gt 0 ]; then
        echo -e "${YELLOW}⚠ Note: ${SKIPPED} test(s) were skipped (likely optional features not configured)${NC}"
    fi
    exit 0
else
    echo -e "${RED}✗ Some tests failed. Review the output above.${NC}"
    exit 1
fi

