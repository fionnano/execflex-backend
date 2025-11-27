# ExecFlex API Test Suite

This directory contains regression tests for the ExecFlex API.

## Files

- **`test_api.sh`** - Main regression test suite (16 tests covering all endpoints)
- **`test_api_examples.sh`** - Quick reference with example curl commands
- **`TEST_API.md`** - Comprehensive documentation

## Quick Start

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

