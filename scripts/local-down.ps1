<#
.SYNOPSIS
  Delete the local k3d cluster (and everything in it: ArgoCD, Grafana, photos, database).

.EXAMPLE
  .\scripts\local-down.ps1
#>
[CmdletBinding()]
param([string]$ClusterName = "event-album")

$ErrorActionPreference = "Stop"

if (-not (Get-Command k3d -ErrorAction SilentlyContinue)) {
    Write-Host "k3d not found - nothing to delete." -ForegroundColor Yellow
    exit 0
}

Write-Host "Deleting k3d cluster '$ClusterName' (all data in it is lost)..." -ForegroundColor Cyan
k3d cluster delete $ClusterName
if ($LASTEXITCODE -ne 0) { throw "k3d cluster delete failed" }
Write-Host "Done. Create it again with .\scripts\local-up.ps1" -ForegroundColor Green
Write-Host "Tip: to only pause it and keep the data, use:  k3d cluster stop $ClusterName"
