"""
Dummy configuration so the suite can import the app without real credentials.

setdefault leaves a developer's already-exported values alone. These are not
secrets and are not used outside the test process.

The test directory is put on sys.path so ``import jwt_tokens`` works. The
stdlib package named ``test`` would otherwise hide this directory.
"""
import os
import sys

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
if _TEST_DIR not in sys.path:
    sys.path.insert(0, _TEST_DIR)

os.environ.setdefault("SUPABASE_URL", "https://test-project.supabase.co")
os.environ.setdefault("SUPABASE_JWT_ISS", "https://test-project.supabase.co/auth/v1")
os.environ.setdefault(
    "SUPABASE_JWT_SECRET",
    "test-supabase-jwt-secret-do-not-use-prod-01",
)
os.environ.setdefault(
    "SUPABASE_SERVICE_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIn0.dGVzdA",
)
os.environ.setdefault("APP_ENV", "test")
