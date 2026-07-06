# ExecFlex local dev — starts backend + frontend in parallel
# Usage: .\start-local.ps1
# Ctrl+C stops both processes

$ErrorActionPreference = "Stop"

# ── Backend env overrides ───────────────────────────────────────────
$env:VOICE_MONITOR_ENABLED = "false"
$env:APP_ENV = "dev"
$env:PYTHONIOENCODING = "utf-8"

# All AI feature flags ON
$env:EXECFLEX_AI_MATCH_RERANK = "1"
$env:EXECFLEX_AI_SCREENING_SUMMARY = "1"
$env:EXECFLEX_AI_CV_PARSER = "1"
$env:EXECFLEX_AI_JD_GENERATOR = "1"
$env:EXECFLEX_AI_QUESTION_FLOW = "1"
$env:EXECFLEX_AI_COMPLIANCE_CHECK = "1"

$backendDir = $PSScriptRoot
$frontendDir = Join-Path (Split-Path $backendDir) "execo-bridge"

# ── Ensure backend venv exists ──────────────────────────────────────
$venv = Join-Path $backendDir ".venv\Scripts\python.exe"
if (-not (Test-Path $venv)) {
    Write-Host "[setup] Creating Python venv..."
    python -m venv (Join-Path $backendDir ".venv")
    & $venv -m pip install -q -r (Join-Path $backendDir "requirements.txt")
    & $venv -m pip install -q flask-limiter
}

# ── Start backend ──────────────────────────────────────────────────
Write-Host ""
Write-Host "=== ExecFlex Local Dev ==="
Write-Host "  Backend:  http://localhost:5001"
Write-Host "  Frontend: http://localhost:8080  (Vite may pick next free port)"
Write-Host "  AI flags: ALL ON"
Write-Host "  Press Ctrl+C to stop both"
Write-Host ""

$backend = Start-Process -NoNewWindow -PassThru -FilePath $venv `
    -ArgumentList (Join-Path $backendDir "server.py") `
    -WorkingDirectory $backendDir

# ── Start frontend ─────────────────────────────────────────────────
$frontend = Start-Process -NoNewWindow -PassThru -FilePath "npm" `
    -ArgumentList "run","dev" `
    -WorkingDirectory $frontendDir

try {
    # Wait for either process to exit
    while (-not $backend.HasExited -and -not $frontend.HasExited) {
        Start-Sleep -Milliseconds 500
    }
} finally {
    # Clean up both on exit
    if (-not $backend.HasExited) { Stop-Process -Id $backend.Id -Force -ErrorAction SilentlyContinue }
    if (-not $frontend.HasExited) { Stop-Process -Id $frontend.Id -Force -ErrorAction SilentlyContinue }
    Write-Host "`nStopped."
}
