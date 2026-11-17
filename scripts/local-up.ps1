<#
.SYNOPSIS
  Create the local Kubernetes environment for Event Album on Windows (k3d + ArgoCD).

.DESCRIPTION
  1. Checks the tools (docker, k3d, kubectl, cloudflared) and tells you how to install missing ones.
  2. Creates a k3d cluster (k3s inside Docker) with host port 8080 -> cluster port 80 (Traefik).
  3. Creates the secrets that are never stored in git.
  4. Installs ArgoCD and applies the root "app of apps" (argocd/root-app.yaml).
     ArgoCD then installs KEDA, kube-prometheus-stack and the app itself from GitHub (branch main).
  5. Waits for the app and prints the URLs and passwords.

  Safe to run again: existing cluster and secrets are kept.

.PARAMETER LocalImages
  Build the api/worker images on this PC and import them into the cluster, instead of pulling
  them from GHCR. Use this on the first run, before CI has pushed any image to ghcr.io.

.PARAMETER GitHubToken
  Only for a PRIVATE GitHub repo: a fine-grained token with read-only "Contents" permission
  on the repo, so ArgoCD can read it. Not needed for a public repo.

.EXAMPLE
  .\scripts\local-up.ps1 -LocalImages
.EXAMPLE
  .\scripts\local-up.ps1 -GitHubToken github_pat_xxx
#>
[CmdletBinding()]
param(
    [switch]$LocalImages,
    [string]$GitHubToken = "",
    [string]$ClusterName = "event-album",
    [int]$HttpPort = 8080
)

$ErrorActionPreference = "Stop"

# ---- pinned versions (bump deliberately) ----
$K3sImage      = "rancher/k3s:v1.36.6-k3s1"
$ArgoCdVersion = "v3.5.7"
$RepoUrl       = "https://github.com/Itay-nomis/event-album"

$RepoRoot = Split-Path -Parent $PSScriptRoot

function Write-Step([string]$Text) { Write-Host "`n==> $Text" -ForegroundColor Cyan }
function Write-Ok([string]$Text)   { Write-Host "    $Text" -ForegroundColor Green }
function Write-Note([string]$Text) { Write-Host "    $Text" -ForegroundColor Yellow }

# Run a native command and stop the script if it fails.
function Invoke-Native([string]$What, [scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)" }
}

# Run a native command only to see whether it succeeds (its error output is hidden).
function Test-Native([scriptblock]$Command) {
    $ErrorActionPreference = "Continue"   # local to this function
    & $Command *> $null
    return ($LASTEXITCODE -eq 0)
}

function Get-RandomHex([int]$Bytes = 24) {
    $buffer = New-Object byte[] $Bytes
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($buffer)
    return (($buffer | ForEach-Object { $_.ToString("x2") }) -join "")
}

function ConvertFrom-Base64([string]$Value) {
    return [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($Value))
}

# Apply YAML text through kubectl (idempotent create-or-update).
function Invoke-KubectlApply([string]$What, [string]$Yaml) {
    $Yaml | kubectl apply -f -
    if ($LASTEXITCODE -ne 0) { throw "$What failed" }
}

# ---------------------------------------------------------------------------
Write-Step "Checking prerequisites"
$tools = @(
    @{ Name = "docker";      Winget = "Docker.DockerDesktop";  Why = "runs the k3d cluster (Docker Desktop)" },
    @{ Name = "k3d";         Winget = "k3d.k3d";               Why = "creates the local Kubernetes cluster" },
    @{ Name = "kubectl";     Winget = "Kubernetes.kubectl";    Why = "talks to Kubernetes" },
    @{ Name = "cloudflared"; Winget = "Cloudflare.cloudflared"; Why = "public HTTPS link (only needed for tunnel.ps1)"; Optional = $true }
)
$missing = $false
foreach ($t in $tools) {
    if (Get-Command $t.Name -ErrorAction SilentlyContinue) {
        Write-Ok "$($t.Name) found"
    } elseif ($t.Optional) {
        Write-Note "$($t.Name) not found ($($t.Why)). Install: winget install --id $($t.Winget) -e"
    } else {
        Write-Host "    $($t.Name) NOT found - $($t.Why)." -ForegroundColor Red
        Write-Host "      Install: winget install --id $($t.Winget) -e   (then open a NEW PowerShell window)"
        $missing = $true
    }
}
if ($missing) { throw "Install the missing tools above and run this script again." }

if (-not (Test-Native { docker info })) {
    throw "Docker is installed but not running. Start Docker Desktop, wait until it says 'Engine running', then retry."
}
Write-Ok "Docker engine is running"

# ---------------------------------------------------------------------------
Write-Step "Cluster '$ClusterName'"
$clusterExists = Test-Native { k3d cluster get $ClusterName }
if ($clusterExists) {
    Write-Ok "already exists - reusing it"
    Invoke-Native "k3d cluster start" { k3d cluster start $ClusterName }
} else {
    # -p maps localhost:$HttpPort to port 80 of k3d's load balancer -> Traefik ingress
    Invoke-Native "k3d cluster create" {
        k3d cluster create $ClusterName --image $K3sImage --servers 1 --agents 0 `
            -p "$($HttpPort):80@loadbalancer" --wait
    }
}
Invoke-Native "kubectl context" { kubectl config use-context "k3d-$ClusterName" }

# Trust X-Forwarded-* in Traefik so the tunnel's https scheme reaches the app (QR links)
Invoke-Native "Traefik config" { kubectl apply -f "$RepoRoot\k8s\cluster\traefik-forwarded-headers.yaml" }

# ---------------------------------------------------------------------------
if ($LocalImages) {
    Write-Step "Building images locally and importing them into the cluster"
    # Use exactly the image names/tags that ArgoCD will deploy from git, so the cluster finds
    # them already present and does not try to pull from ghcr.io.
    $kustomization = Get-Content "$RepoRoot\k8s\overlays\local\kustomization.yaml" -Raw
    $images = [regex]::Matches($kustomization, "newName:\s*(\S+)\s+newTag:\s*(\S+)") |
        ForEach-Object { "$($_.Groups[1].Value):$($_.Groups[2].Value)" }
    if ($images.Count -ne 2) { throw "Could not read the two image names from k8s/overlays/local/kustomization.yaml" }
    Write-Note "The tag must match the one on GitHub's main branch: run 'git pull' first if unsure."
    foreach ($image in $images) {
        $service = if ($image -like "*-worker:*") { "worker" } else { "api" }
        Write-Ok "docker build $image"
        Invoke-Native "docker build $service" { docker build -t $image "$RepoRoot\$service" }
    }
    Invoke-Native "k3d image import" { k3d image import @images -c $ClusterName }
}

# ---------------------------------------------------------------------------
Write-Step "Secrets (created here, never stored in git)"
foreach ($ns in @("event-album", "monitoring", "argocd")) {
    Invoke-KubectlApply "namespace $ns" (kubectl create namespace $ns --dry-run=client -o yaml | Out-String)
}

if (Test-Native { kubectl -n event-album get secret event-album-secrets }) {
    Write-Ok "event-album-secrets already exists - keeping it"
} else {
    $envFile = "$RepoRoot\k8s\overlays\local\secrets.env"
    if (Test-Path $envFile) {
        Invoke-Native "create app secret" {
            kubectl -n event-album create secret generic event-album-secrets --from-env-file="$envFile"
        }
        Write-Ok "created from k8s\overlays\local\secrets.env"
    } else {
        $pgPassword = Get-RandomHex 24
        Invoke-Native "create app secret" {
            kubectl -n event-album create secret generic event-album-secrets `
                --from-literal=POSTGRES_PASSWORD=$pgPassword `
                --from-literal=DATABASE_URL="postgresql://album:$pgPassword@postgres:5432/album" `
                --from-literal=S3_ACCESS_KEY=local `
                --from-literal=S3_SECRET_KEY=local
        }
        Write-Ok "created with a random Postgres password"
    }
}

if (Test-Native { kubectl -n monitoring get secret grafana-admin }) {
    Write-Ok "grafana-admin already exists - keeping it"
} else {
    $grafanaPassword = Get-RandomHex 12
    Invoke-Native "create grafana secret" {
        kubectl -n monitoring create secret generic grafana-admin `
            --from-literal=admin-user=admin --from-literal=admin-password=$grafanaPassword
    }
    Write-Ok "grafana-admin created"
}

# ---------------------------------------------------------------------------
Write-Step "Installing ArgoCD $ArgoCdVersion"
# --server-side: ArgoCD's CRDs are too large for a classic client-side apply
Invoke-Native "ArgoCD install" {
    kubectl apply -n argocd --server-side --force-conflicts `
        -f "https://raw.githubusercontent.com/argoproj/argo-cd/$ArgoCdVersion/manifests/install.yaml"
}
Invoke-Native "wait for argocd-server" { kubectl -n argocd rollout status deploy/argocd-server --timeout=300s }
Invoke-Native "wait for argocd-repo-server" { kubectl -n argocd rollout status deploy/argocd-repo-server --timeout=300s }
Invoke-Native "wait for argocd-application-controller" {
    kubectl -n argocd rollout status statefulset/argocd-application-controller --timeout=300s
}

if ($GitHubToken) {
    Write-Step "Registering the private repo in ArgoCD"
    # A Secret with this label is how ArgoCD stores repository credentials
    $repoSecret = @"
apiVersion: v1
kind: Secret
metadata:
  name: repo-event-album
  namespace: argocd
  labels:
    argocd.argoproj.io/secret-type: repository
stringData:
  type: git
  url: $RepoUrl
  username: git
  password: $GitHubToken
"@
    Invoke-KubectlApply "repo credential" $repoSecret
    Write-Ok "repo credential stored"
}

# ---------------------------------------------------------------------------
Write-Step "Applying the root app (GitOps takes over from here)"
Invoke-Native "apply root app" { kubectl apply -f "$RepoRoot\argocd\root-app.yaml" }

Write-Step "Waiting for ArgoCD to deploy the app (first run downloads images: 5-10 minutes)"
$deadline = (Get-Date).AddMinutes(15)
while (-not (Test-Native { kubectl -n event-album get deploy api })) {
    if ((Get-Date) -gt $deadline) { break }
    Write-Host "    ...waiting for ArgoCD to create the api Deployment" -ForegroundColor DarkGray
    Start-Sleep -Seconds 10
}
$apiReady = Test-Native { kubectl -n event-album rollout status deploy/api --timeout=600s }
if ($apiReady) {
    Write-Ok "api is ready"
} else {
    Write-Note "The api is not ready yet. Look at what is going on with:"
    Write-Note "  kubectl -n argocd get applications"
    Write-Note "  kubectl -n event-album get pods"
    Write-Note "  kubectl -n event-album describe pod -l app=event-album-api"
    Write-Note "ImagePullBackOff? CI has not pushed images yet, or the GHCR packages are private:"
    Write-Note "  run again with -LocalImages, or make the packages public (docs/GUIDE.he.md)."
}

# ---------------------------------------------------------------------------
$argoPassword = ""
if (Test-Native { kubectl -n argocd get secret argocd-initial-admin-secret }) {
    $argoPassword = ConvertFrom-Base64 (kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}")
}
$grafanaPw = ConvertFrom-Base64 (kubectl -n monitoring get secret grafana-admin -o jsonpath="{.data.admin-password}")

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " Event Album:  http://localhost:$HttpPort"
Write-Host " Public link:  .\scripts\tunnel.ps1   (then open the https://...trycloudflare.com URL)"
Write-Host ""
Write-Host " ArgoCD:       kubectl -n argocd port-forward svc/argocd-server 8081:443"
Write-Host "               https://localhost:8081   user: admin   password: $argoPassword"
Write-Host " Grafana:      kubectl -n monitoring port-forward svc/kube-prometheus-stack-grafana 3000:80"
Write-Host "               http://localhost:3000    user: admin   password: $grafanaPw"
Write-Host " Prometheus:   kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090:9090"
Write-Host ""
Write-Host " Watch it:     kubectl -n event-album get pods -w"
Write-Host " Remove all:   .\scripts\local-down.ps1"
Write-Host "============================================================" -ForegroundColor Cyan
