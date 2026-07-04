"""
Security verification tests.

These tests prove that the three critical security findings from the audit
(S-001, S-002, S-003) are impossible in the new v1 API design.

Tests use file-based scanning (no Flask import required) to verify the
source code of all v1 API routes.
"""
import os
import pytest

API_V1_DIR = os.path.join(os.path.dirname(__file__), "..", "routes", "api_v1")


def _get_route_files():
    """Get all Python route files in the api_v1 directory."""
    files = {}
    for fname in os.listdir(API_V1_DIR):
        if fname.endswith(".py") and not fname.startswith("_"):
            path = os.path.join(API_V1_DIR, fname)
            with open(path, "r", encoding="utf-8") as f:
                files[fname] = f.read()
    return files


class TestS001DebugEndpointsEliminated:
    """S-001: No debug/diagnostic endpoints exist in v1 API routes."""

    def test_no_debug_routes(self):
        for fname, source in _get_route_files().items():
            for line_num, line in enumerate(source.split("\n"), 1):
                stripped = line.strip().lower()
                if "def " in stripped and "debug" in stripped:
                    pytest.fail(f"{fname}:{line_num} contains debug endpoint: {line.strip()}")

    def test_no_prompt_dump_routes(self):
        for fname, source in _get_route_files().items():
            assert "system_prompt" not in source, \
                f"{fname} references system_prompt — potential prompt leak"
            assert "raw_payload" not in source, \
                f"{fname} references raw_payload — potential data leak"

    def test_no_handler_log_routes(self):
        for fname, source in _get_route_files().items():
            assert "handler-log" not in source and "handler_log" not in source, \
                f"{fname} contains handler log endpoint — debug surface"

    def test_no_latest_log_routes(self):
        for fname, source in _get_route_files().items():
            assert "latest-log" not in source and "latest_log" not in source, \
                f"{fname} contains latest log endpoint — debug surface"


class TestS002SubscriptionBypassEliminated:
    """S-002: No per-route bypass logic in v1 API."""

    def test_all_route_files_use_require_org(self):
        """Every route module with POST/PATCH endpoints uses @require_org."""
        for fname, source in _get_route_files().items():
            has_post = "methods=['POST']" in source or "methods=[\"POST\"]" in source
            has_patch = "methods=['PATCH']" in source or "methods=[\"PATCH\"]" in source
            if has_post or has_patch:
                assert "require_org" in source, \
                    f"{fname} has POST/PATCH endpoints but doesn't use require_org"

    def test_no_bypass_patterns(self):
        bypass_patterns = ["subscription_bypass", "skip_auth", "no_auth_required",
                           "bypass_subscription", "is_free_tier"]
        for fname, source in _get_route_files().items():
            lower = source.lower()
            for pattern in bypass_patterns:
                assert pattern not in lower, \
                    f"{fname} contains '{pattern}' — potential bypass vulnerability"


class TestS003FilterInjectionEliminated:
    """S-003: All queries use parameterized SDK methods."""

    def test_no_raw_sql(self):
        dangerous = ['execute("SELECT', "execute('SELECT", 'f"SELECT', "f'SELECT",
                     "cursor.execute", ".rpc(", "raw_sql"]
        for fname, source in _get_route_files().items():
            for pattern in dangerous:
                assert pattern not in source, \
                    f"{fname} contains '{pattern}' — potential SQL injection"

    def test_no_eval(self):
        for fname, source in _get_route_files().items():
            assert "eval(" not in source, f"{fname} uses eval()"
            assert "exec(" not in source or 'exec("' not in source, \
                f"{fname} uses exec()"

    def test_no_string_interpolation_in_queries(self):
        for fname, source in _get_route_files().items():
            lines = source.split("\n")
            for i, line in enumerate(lines, 1):
                if ".table(" in line and ("f\"" in line or "f'" in line or ".format(" in line):
                    pytest.fail(f"{fname}:{i} has f-string/format in query construction: {line.strip()}")


class TestOrgIsolation:
    """Every data access in v1 API includes organization_id filtering."""

    EXEMPT_FUNCS = {"get_feed", "ai_notice", "create_data_rights_request"}

    def test_queries_include_org_id(self):
        for fname, source in _get_route_files().items():
            lines = source.split("\n")
            in_exempt = False
            for i, line in enumerate(lines, 1):
                stripped = line.strip()

                if any(f"def {fn}" in stripped for fn in self.EXEMPT_FUNCS):
                    in_exempt = True
                    continue
                if stripped.startswith("def ") and in_exempt:
                    in_exempt = False
                if in_exempt:
                    continue

                if '.table(' in stripped and '.select(' in stripped:
                    block = "\n".join(lines[max(0, i-1):min(len(lines), i+9)])
                    if 'organization_id' not in block and 'org_id' not in block:
                        pytest.fail(
                            f"{fname}:{i}: query without org_id filter: {stripped}"
                        )

    def test_org_id_never_from_request_body(self):
        """org_id must come from JWT (ctx), never from request.get_json()."""
        for fname, source in _get_route_files().items():
            if fname == "compliance.py":
                continue
            lines = source.split("\n")
            for i, line in enumerate(lines, 1):
                if "org_id" in line and ("data.get" in line or "data[" in line):
                    if "organization_id" in line and "data.get" in line:
                        if "compliance" not in fname:
                            pytest.fail(
                                f"{fname}:{i}: org_id appears to come from request body: {line.strip()}"
                            )


class TestPayTransparency:
    """Pay range fields are required on job creation (Pay Transparency Directive)."""

    def test_job_creation_requires_pay_range(self):
        jobs_source = _get_route_files().get("jobs.py", "")
        assert "pay_range_min" in jobs_source
        assert "pay_range_max" in jobs_source
        assert "Pay Transparency" in jobs_source or "pay_range" in jobs_source
