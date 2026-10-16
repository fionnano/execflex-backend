#!/bin/bash

# Quick examples of manual curl commands for testing the API
# These are the same tests that test_api.sh runs automatically

API_BASE="${1:-https://api.execflex.ai}"

echo "Testing API at: ${API_BASE}"
echo ""

# Health Check
echo "1. Health Check:"
curl -s "${API_BASE}/"
echo -e "\n"

# View Roles
echo "2. View Roles:"
curl -s "${API_BASE}/view-roles" | jq '.' 2>/dev/null || curl -s "${API_BASE}/view-roles"
echo -e "\n"

# (Legacy POST /match removed 2026-10-16 — SEC-E-005)

# Post Role
echo "4. Post Role:"
curl -s -X POST "${API_BASE}/post-role" \
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
  }' | jq '.' 2>/dev/null || curl -s -X POST "${API_BASE}/post-role" \
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
echo -e "\n"

