"""Dispatcher build script and the worker's import surface.

The live worker (Render service execflex-call-dispatcher) runs
`python -m workers.call_dispatcher --continuous` and calls
process_queued_jobs() directly. It must not grow an HTTP dependency on
POST /onboarding/process-jobs, and the modules it imports must not require
agentic-core symbols. The private pin is install-time only, via
scripts/render_build.sh.
"""
import ast
import os
import subprocess
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT = os.path.join(ROOT, "scripts", "render_build.sh")
DISPATCHER = os.path.join(ROOT, "workers", "call_dispatcher.py")

# The rewrite that already succeeds on execflex-backend-1.
GIT_REWRITE = (
    'git config --global url."https://${GITHUB_TOKEN}@github.com/".insteadOf '
    '"https://github.com/"'
)


def _module_file(module: str):
    parts = module.split(".")
    candidate = os.path.join(ROOT, *parts)
    py = candidate + ".py"
    if os.path.isfile(py):
        return py
    init = os.path.join(candidate, "__init__.py")
    if os.path.isfile(init):
        return init
    return None


def _imported_modules(path):
    with open(path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            if node.module:
                found.append(node.module)
    return found


def _local_import_closure(start_path):
    seen = set()
    stack = [start_path]
    while stack:
        path = stack.pop()
        if path in seen:
            continue
        seen.add(path)
        for module in _imported_modules(path):
            local = _module_file(module)
            if local is not None:
                stack.append(local)
    return seen


def test_render_build_script_refuses_without_token():
    env = os.environ.copy()
    env.pop("GITHUB_TOKEN", None)
    result = subprocess.run(
        ["bash", SCRIPT],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "GITHUB_TOKEN" in result.stderr
    assert "pip install" not in result.stdout
    # A missing token must fail before git config rewrites anything.
    assert "insteadOf" not in result.stderr


def test_render_build_script_matches_working_web_build():
    with open(SCRIPT, "r", encoding="utf-8") as fh:
        source = fh.read()
    assert source.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in source
    assert "set -x" not in source
    assert GIT_REWRITE in source
    assert "exec pip install -r requirements.txt" in source
    assert os.access(SCRIPT, os.X_OK)


def test_dispatcher_calls_process_queued_jobs_in_process():
    with open(DISPATCHER, "r", encoding="utf-8") as fh:
        source = fh.read()
    assert "from services.onboarding_service import process_queued_jobs" in source
    assert "process_queued_jobs(limit=limit)" in source
    assert "--continuous" in source
    assert "/onboarding/process-jobs" not in source
    assert "X-Process-Jobs-Secret" not in source
    assert "requests." not in source
    assert "urllib" not in source


def test_dispatcher_import_closure_does_not_touch_agentic_core():
    closure = _local_import_closure(DISPATCHER)
    rels = sorted(os.path.relpath(path, ROOT) for path in closure)
    assert "workers/call_dispatcher.py" in rels
    assert "services/onboarding_service.py" in rels
    blob = []
    for path in closure:
        with open(path, "r", encoding="utf-8") as fh:
            blob.append(fh.read())
    combined = "\n".join(blob)
    assert "agentic_core" not in combined
    assert "agentic-core" not in combined


def test_render_build_script_syntax():
    result = subprocess.run(
        ["bash", "-n", SCRIPT],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
