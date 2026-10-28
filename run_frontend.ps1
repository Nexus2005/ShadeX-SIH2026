# Resilient AI — start frontend (Next.js on :3000)
$frontend = Join-Path $PSScriptRoot "frontend"

if (-not (Test-Path (Join-Path $frontend "node_modules"))) {
    Write-Host "Installing frontend dependencies ..." -ForegroundColor Yellow
    Push-Location $frontend
    npm install
    Pop-Location
}

Write-Host "Starting Resilient AI frontend on http://localhost:3000 ..." -ForegroundColor Cyan
npm run dev --prefix $frontend
