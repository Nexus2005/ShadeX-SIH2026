# Resilient AI — start backend (:8000) and frontend (:3000) in background windows
$root    = $PSScriptRoot
$backend = Join-Path $root "backend"
$python  = Join-Path $backend ".venv\Scripts\python.exe"
$frontend = Join-Path $root "frontend"

if (-not (Test-Path $python)) {
    Write-Error "Virtualenv not found: $python"
    exit 1
}

$backendProc = Start-Process -FilePath $python `
    -ArgumentList "-m","uvicorn","app.main:app","--host","0.0.0.0","--port","8000" `
    -WorkingDirectory $backend -WindowStyle Minimized -PassThru

$frontendProc = Start-Process -FilePath "cmd.exe" `
    -ArgumentList "/c","npm","run","dev" `
    -WorkingDirectory $frontend -WindowStyle Minimized -PassThru

Write-Host "Backend  PID $($backendProc.Id) -> http://127.0.0.1:8000/api/health" -ForegroundColor Cyan
Write-Host "Frontend PID $($frontendProc.Id) -> http://localhost:3000" -ForegroundColor Cyan

$ok = $false
foreach ($i in 1..30) {
    Start-Sleep -Seconds 2
    try {
        $h = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/health" -TimeoutSec 3
        if ($h.ok) { Write-Host "Backend healthy." -ForegroundColor Green; $ok = $true; break }
    } catch { }
}
if (-not $ok) { Write-Warning "Backend did not answer within 60s - check the backend window." }

foreach ($i in 1..45) {
    try {
        $f = Invoke-WebRequest -Uri "http://localhost:3000/login" -UseBasicParsing -TimeoutSec 3
        if ($f.StatusCode -eq 200) { Write-Host "Frontend ready." -ForegroundColor Green; break }
    } catch { Start-Sleep -Seconds 2 }
}

Write-Host ""
Write-Host "Open http://localhost:3000 and sign in: admin@resilient-ai.local / Password123!" -ForegroundColor Yellow
