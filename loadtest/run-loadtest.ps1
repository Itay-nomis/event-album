<#
.SYNOPSIS
  Simulate a 300-guest wedding against the local Event Album (k6 in Docker, nothing to install).

.DESCRIPTION
  1. Generates ~2-3 MB test photos once (inside a python container) into loadtest\images.
  2. Runs loadtest\wedding.js with the grafana/k6 image.
  The container reaches your PC's localhost through host.docker.internal.

.PARAMETER BaseUrl
  Default http://host.docker.internal:8080 (the k3d cluster). For docker compose use :8000.
  Avoid pointing it at the trycloudflare.com tunnel: quick tunnels are for light testing.

.PARAMETER TimeScale
  1 = the full 10-minute evening. 0.1 = a 1-minute smoke test.

.PARAMETER Speed
  Multiplies the upload rate. 2 = a 600-guest wedding.

.EXAMPLE
  .\loadtest\run-loadtest.ps1
.EXAMPLE
  .\loadtest\run-loadtest.ps1 -TimeScale 0.1
#>
[CmdletBinding()]
param(
    [string]$BaseUrl = "http://host.docker.internal:8080",
    [double]$TimeScale = 1,
    [double]$Speed = 1
)

$ErrorActionPreference = "Stop"
$K6Image     = "grafana/k6:1.8.2"
$PythonImage = "python:3.12.16-slim"
$Here = $PSScriptRoot

if (-not (Test-Path "$Here\images\index.json")) {
    Write-Host "Generating test photos (one time)..." -ForegroundColor Cyan
    docker run --rm -v "$($Here):/work" -w /work $PythonImage `
        sh -c "pip install --quiet --disable-pip-version-check pillow==11.1.0 && python generate_images.py images 24"
    if ($LASTEXITCODE -ne 0) { throw "Generating images failed" }
}

$scale = [string]::Format([System.Globalization.CultureInfo]::InvariantCulture, "{0}", $TimeScale)
$speedText = [string]::Format([System.Globalization.CultureInfo]::InvariantCulture, "{0}", $Speed)

Write-Host ""
Write-Host "Running the wedding against $BaseUrl (TimeScale=$scale, Speed=$speedText)" -ForegroundColor Cyan
Write-Host "In other windows, watch the autoscaling:" -ForegroundColor Cyan
Write-Host "  kubectl -n event-album get pods -w"
Write-Host "  kubectl -n event-album get hpa -w        # keda-hpa-worker = KEDA, api = CPU HPA"
Write-Host "  Grafana -> dashboard 'Event Album' (queue length vs worker replicas)"
Write-Host ""

docker run --rm -i -v "$($Here):/scripts" -w /scripts `
    -e BASE_URL=$BaseUrl -e TIME_SCALE=$scale -e SPEED=$speedText `
    $K6Image run wedding.js
if ($LASTEXITCODE -ne 0) { throw "k6 reported failed thresholds or errors (see summary above)" }
