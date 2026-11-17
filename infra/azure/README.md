# Azure infrastructure (Terraform) - for later

> **This costs real money.** Nothing in this repo applies it automatically: CI only runs
> `terraform fmt -check` and `terraform validate` (no Azure login, no state, nothing created).
> The free path for this project is local Kubernetes + Cloudflare quick tunnel (see the main README).

## What it creates

| Resource | Setting chosen to keep cost down | Rough cost* |
|---|---|---|
| Resource group `rg-evalbum` | - | free |
| AKS cluster | **Free** control-plane tier, **1 node** `Standard_B2s`, KEDA add-on, OIDC issuer + workload identity | node VM + disk ≈ $1-1.5/day |
| Storage account + private `photos` container | Standard LRS | cents |
| User-assigned identity + federated credential + `Storage Blob Data Contributor` role | lets the pods use Blob Storage with no keys | free |
| PostgreSQL Flexible Server | `B_Standard_B1ms`, 32 GB P4 storage, no HA, no geo-backup | ≈ $0.5-1/day |
| Budget on the resource group | emails at 50 % and 80 % actual, 100 % forecast of `budget_amount` | free |

\*Approximate pay-as-you-go prices; check the [Azure pricing calculator](https://azure.microsoft.com/pricing/calculator/)
for your region. A public LoadBalancer IP for Traefik adds a little more.

## Rules

1. **Use the free trial credit** (new Azure accounts get USD 200 for 30 days). Do the whole stage within it.
2. **Always `terraform destroy` when you finish a session.** A cluster left running for a month costs far more than the budget.
3. Keep the **budget alert** email real - it is your smoke alarm. (Budgets alert; they do not stop spending.)
4. State is stored locally (`terraform.tfstate`, gitignored) and contains the DB password. Don't commit it, don't lose it
   before you have destroyed everything (without state, delete the resource group in the portal instead).

## Run it (PowerShell)

```powershell
winget install --id Microsoft.AzureCLI -e
winget install --id Hashicorp.Terraform -e
winget install --id Helm.Helm -e          # used below to install Traefik on AKS
az login
cd infra\azure
copy terraform.tfvars.example terraform.tfvars   # set budget_email
terraform init
terraform plan -out tfplan        # read the plan: what will be created?
terraform apply tfplan
```

Then deploy the app to AKS:

```powershell
# kubectl -> AKS
Invoke-Expression (terraform output -raw aks_get_credentials)

# Fill the REPLACE_ME values in k8s/overlays/azure/ from:
terraform output storage_account_url
terraform output workload_identity_client_id

# Secret with the database URL (not in git)
kubectl create namespace event-album
kubectl -n event-album create secret generic event-album-secrets `
  --from-literal=DATABASE_URL="$(terraform output -raw database_url)"

# Traefik as ingress controller (AKS has none by default) - creates a public IP
helm repo add traefik https://traefik.github.io/charts
helm install traefik traefik/traefik --version 42.4.1 -n traefik --create-namespace

kubectl apply -k ..\..\k8s\overlays\azure
kubectl -n traefik get svc traefik   # EXTERNAL-IP = your public address (http)
```

KEDA comes from the AKS add-on (`workload_autoscaler_profile.keda_enabled`), so the
`ScaledObject` in `k8s/base` works without installing the Helm chart.

## Clean up

```powershell
terraform destroy
```

Check in the portal that the `rg-evalbum` resource group (and the `MC_rg-evalbum_...` node resource group) are gone.
