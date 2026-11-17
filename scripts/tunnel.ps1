<#
.SYNOPSIS
  Give the local Event Album a free public HTTPS address (Cloudflare quick tunnel).

.DESCRIPTION
  Runs `cloudflared tunnel --url http://localhost:<Port>`. No Cloudflare account needed.
  cloudflared prints a random https://<words>.trycloudflare.com address - open THAT address
  on your PC to create the event, so the QR code points at it, then scan it with a phone.

  The address changes every time you start the tunnel and stops working when you press Ctrl+C.
  Quick tunnels are meant for testing, not production.

.PARAMETER Port
  8080 = the k3d cluster (default). Use 8000 for `docker compose`.

.EXAMPLE
  .\scripts\tunnel.ps1
.EXAMPLE
  .\scripts\tunnel.ps1 -Port 8000
#>
[CmdletBinding()]
param([int]$Port = 8080)

$ErrorActionPreference = "Stop"

if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
    Write-Host "cloudflared not found. Install it with:" -ForegroundColor Red
    Write-Host "  winget install --id Cloudflare.cloudflared -e"
    Write-Host "then open a NEW PowerShell window and run this script again."
    exit 1
}

try {
    $null = Invoke-WebRequest -Uri "http://localhost:$Port/healthz" -UseBasicParsing -TimeoutSec 5
} catch {
    Write-Host "Nothing answers on http://localhost:$Port/healthz yet." -ForegroundColor Yellow
    Write-Host "Start the app first (.\scripts\local-up.ps1 or docker compose up). Continuing anyway..."
}

Write-Host ""
Write-Host "Starting tunnel to http://localhost:$Port ..." -ForegroundColor Cyan
Write-Host "Look for a line like:  https://something-random.trycloudflare.com" -ForegroundColor Cyan
Write-Host "Open it on this PC, create the event, and scan the QR code with your phone." -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop." -ForegroundColor Cyan
Write-Host ""

# The original Host header (the trycloudflare name) is passed through to the app, and
# Cloudflare adds X-Forwarded-Proto / CF-Visitor = https, so the QR code gets the public URL.
cloudflared tunnel --no-autoupdate --url "http://localhost:$Port"
