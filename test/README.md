# ExecFlex API Test Suite

This directory contains regression tests for the ExecFlex API.

## Files

- **`smoke_test.sh`** - Quick smoke test suite for CI/CD pipelines (tests all endpoints for 200 status)
- **`test_api.sh`** - Main regression test suite (16 tests covering all endpoints with error cases)
- **`test_api_examples.sh`** - Quick reference with example curl commands
- **`TEST_API.md`** - Comprehensive documentation

## Quick Start

### Smoke Tests (CI/CD)

```bash
# Quick smoke test (defaults to localhost:5001)
./smoke_test.sh

# Test against local server
./smoke_test.sh http://localhost:5001

# Test against production
./smoke_test.sh https://execflex-backend-1.onrender.com
```

### Full Regression Tests

```bash
# Run the full test suite (defaults to https://api.execflex.ai)
./test_api.sh

# Test against local server (use http:// for local)
./test_api.sh http://localhost:5001

# Test against production (always use https://)
./test_api.sh https://api.execflex.ai
```

**Note:** Always use `https://` for production URLs to avoid 307 redirects.

See [TEST_API.md](./TEST_API.md) for detailed documentation.

