# Resilient AI — start backend (FastAPI/uvicorn on :8000)
$backend = Join-Path $PSScriptRoot "backend"
$python = Join-Path $backend ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Error "Virtualenv not found at $python - create it or run: python -m venv backend\.venv"
    exit 1
}

Write-Host "Starting Resilient AI backend on http://127.0.0.1:8000 ..." -ForegroundColor Cyan
Push-Location $backend
try {
    & $python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
} finally {
    Pop-Location
}
