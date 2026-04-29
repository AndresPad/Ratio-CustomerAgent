<#
.SYNOPSIS
    Start all services for local development.

.DESCRIPTION
    Starts the following services:
      - ratio-mcp        (MCP server)  → port 8000
      - customer-agent    (FastAPI)     → port 8503
      - ratio-ui-web     (React/Vite) → port 3010

    Each service runs in its own PowerShell job.
    Press Ctrl+C to stop all services.

.EXAMPLE
    .\scripts\start_all.ps1
    .\scripts\start_all.ps1 -SkipFrontend
    .\scripts\start_all.ps1 -StopOnly
#>
param(
    [switch]$SkipFrontend,
    [switch]$StopOnly
)

$ErrorActionPreference = "SilentlyContinue"
$ROOT = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$VENV = Join-Path $ROOT ".venv\Scripts\Activate.ps1"

# -- Colors --
function Write-Header($msg) { Write-Host "`n--- $msg ---" -ForegroundColor Cyan }
function Write-Ok($msg)     { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Skip($msg)   { Write-Host "  [SKIP] $msg (skipped)" -ForegroundColor DarkGray }
function Write-Err($msg)    { Write-Host "  [ERR] $msg" -ForegroundColor Red }

# ── Stop existing services ──
Write-Header "Stopping existing services"
$ports = @(
    @{ Port=8000; Name="ratio-mcp" },
    @{ Port=8503; Name="customer-agent" },
    @{ Port=3010; Name="ratio-ui-web" }
)
foreach ($svc in $ports) {
    $conns = Get-NetTCPConnection -LocalPort $svc.Port -State Listen -ErrorAction SilentlyContinue
    if ($conns) {
        foreach ($c in $conns) {
            Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
        }
        Write-Ok "Stopped $($svc.Name) (port $($svc.Port))"
    } else {
        Write-Ok "$($svc.Name) - not running"
    }
}

if ($StopOnly) {
    Write-Header "All services stopped"
    return
}

Start-Sleep -Seconds 1

# ── Start services ──
Write-Header "Starting services"

$jobs = @()

# 0. RATIO MCP server (port 8000)
$jobs += Start-Job -Name "ratio-mcp" -ScriptBlock {
    param($root, $venv)
    Set-Location (Join-Path $root "Code\RATIO_MCP\src")
    & $venv
    $env:PYTHONPATH = (Join-Path $root "Code\RATIO_MCP\src")
    python server.py 2>&1
} -ArgumentList $ROOT, $VENV
Write-Ok "ratio-mcp - http://127.0.0.1:8000"

# 1. CustomerAgent server (port 8503)
$jobs += Start-Job -Name "customer-agent" -ScriptBlock {
    param($root, $venv)
    Set-Location (Join-Path $root "Code\CustomerAgent\src")
    & $venv
    $env:PYTHONPATH = (Join-Path $root "Code\CustomerAgent\src")
    python -m uvicorn server.app:app --host 127.0.0.1 --port 8503 2>&1
} -ArgumentList $ROOT, $VENV
Write-Ok "customer-agent - http://127.0.0.1:8503"

# 2. React UI — Vite dev server (port 3010)
if (-not $SkipFrontend) {
    $jobs += Start-Job -Name "ratio-ui-web" -ScriptBlock {
        param($root)
        Set-Location (Join-Path $root "Code\CustomerAgent\ratio_ui_web")
        npm run dev 2>&1
    } -ArgumentList $ROOT
    Write-Ok "ratio-ui-web - http://127.0.0.1:3010"
} else {
    Write-Skip "ratio-ui-web"
}

# -- Wait for health checks --
Write-Header "Waiting for services to be ready"
Start-Sleep -Seconds 6

$healthChecks = @(
    @{ Url="http://127.0.0.1:8000/health"; Name="ratio-mcp"; Retries=20 },
    @{ Url="http://127.0.0.1:8503/health"; Name="customer-agent"; Retries=10 }
)
foreach ($hc in $healthChecks) {
    $ok = $false
    $maxRetries = if ($hc.Retries) { $hc.Retries } else { 10 }
    for ($i = 0; $i -lt $maxRetries; $i++) {
        try {
            $r = Invoke-RestMethod -Uri $hc.Url -TimeoutSec 2 -ErrorAction Stop
            Write-Ok "$($hc.Name) - healthy"
            $ok = $true
            break
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    if (-not $ok) { Write-Err "$($hc.Name) - failed to start" }
}

# -- Summary --
Write-Header "Local Dev Stack"
Write-Host ""
Write-Host "  Service         URL" -ForegroundColor White
Write-Host "  ----------      ------------------" -ForegroundColor DarkGray
Write-Host "  ratio-mcp       http://127.0.0.1:8000" -ForegroundColor White
Write-Host "  customer-agent  http://127.0.0.1:8503" -ForegroundColor White
if (-not $SkipFrontend) {
    Write-Host "  ratio-ui-web   http://127.0.0.1:3010" -ForegroundColor White
}
Write-Host ""
Write-Host "  Press Ctrl+C to stop all services" -ForegroundColor DarkGray
Write-Host ""

# ── Tail logs (keep script alive) ──
try {
    while ($true) {
        foreach ($j in $jobs) {
            $output = Receive-Job -Job $j -ErrorAction SilentlyContinue
            if ($output) {
                foreach ($line in $output) {
                    Write-Host "[$($j.Name)] $line" -ForegroundColor DarkGray
                }
            }
            if ($j.State -eq 'Failed' -or $j.State -eq 'Completed') {
                Write-Err "$($j.Name) exited ($($j.State))"
                Receive-Job -Job $j -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
            }
        }
        Start-Sleep -Seconds 3
    }
} finally {
    Write-Header "Shutting down all services"
    $jobs | Stop-Job -ErrorAction SilentlyContinue
    $jobs | Remove-Job -Force -ErrorAction SilentlyContinue
    foreach ($svc in $ports) {
        $conns = Get-NetTCPConnection -LocalPort $svc.Port -State Listen -ErrorAction SilentlyContinue
        if ($conns) {
            foreach ($c in $conns) { Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue }
        }
    }
    Write-Ok "All services stopped"
}
