# Event Album on Azure - cost-minimal defaults. NEVER applied by CI; only `validate` runs there.
# Apply by hand, and run `terraform destroy` as soon as you are done (see README.md).

# k8s_*: the Kubernetes ServiceAccount allowed to act as the managed identity
# (must match k8s/overlays/azure/serviceaccount.yaml)
locals {
  name                = var.prefix
  k8s_namespace       = "event-album"
  k8s_service_account = "event-album"
}

# Storage account names are global across Azure -> add a random suffix
resource "random_string" "suffix" {
  length  = 6
  upper   = false
  special = false
}

resource "random_password" "postgres" {
  length  = 32
  special = false # keeps it URL-safe for DATABASE_URL
}

resource "azurerm_resource_group" "main" {
  name     = "rg-${local.name}"
  location = var.location
  tags     = var.tags
}

# ---------- AKS: free control plane, one small node ----------
resource "azurerm_kubernetes_cluster" "main" {
  name                = "aks-${local.name}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  dns_prefix          = local.name
  sku_tier            = "Free" # no uptime SLA, no control-plane charge

  default_node_pool {
    name                        = "system"
    node_count                  = 1
    vm_size                     = var.aks_node_size
    os_disk_size_gb             = 30
    temporary_name_for_rotation = "tmpsystem"
  }

  identity {
    type = "SystemAssigned"
  }

  network_profile {
    network_plugin      = "azure"
    network_plugin_mode = "overlay"
  }

  # Workload identity: pods get Azure AD tokens via federated credentials, no secrets
  oidc_issuer_enabled       = true
  workload_identity_enabled = true

  # Managed KEDA add-on (free) - the ScaledObject in k8s/base works unchanged
  workload_autoscaler_profile {
    keda_enabled = true
  }

  tags = var.tags
}

# ---------- Blob storage for photos ----------
resource "azurerm_storage_account" "photos" {
  name                            = "${local.name}${random_string.suffix.result}"
  resource_group_name             = azurerm_resource_group.main.name
  location                        = azurerm_resource_group.main.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS" # cheapest: 3 copies in one datacenter
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false # photos are served through the API, never public
  tags                            = var.tags
}

resource "azurerm_storage_container" "photos" {
  name                  = "photos"
  storage_account_id    = azurerm_storage_account.photos.id
  container_access_type = "private"
}

# ---------- Identity for the app pods ----------
resource "azurerm_user_assigned_identity" "app" {
  name                = "id-${local.name}-app"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = var.tags
}

# "Tokens from this AKS cluster, for this ServiceAccount, may act as the identity"
resource "azurerm_federated_identity_credential" "app" {
  name                      = "fic-${local.name}-app"
  user_assigned_identity_id = azurerm_user_assigned_identity.app.id
  issuer                    = azurerm_kubernetes_cluster.main.oidc_issuer_url
  subject                   = "system:serviceaccount:${local.k8s_namespace}:${local.k8s_service_account}"
  audience                  = ["api://AzureADTokenExchange"]
}

# Least privilege: read/write blobs in this one storage account only
resource "azurerm_role_assignment" "app_blob" {
  scope                = azurerm_storage_account.photos.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
  principal_type       = "ServicePrincipal"
}

# ---------- Postgres Flexible Server: smallest burstable SKU, minimum storage ----------
resource "azurerm_postgresql_flexible_server" "main" {
  name                          = "psql-${local.name}-${random_string.suffix.result}"
  resource_group_name           = azurerm_resource_group.main.name
  location                      = azurerm_resource_group.main.location
  version                       = "16"
  sku_name                      = var.postgres_sku
  storage_mb                    = 32768
  storage_tier                  = "P4"
  auto_grow_enabled             = false
  backup_retention_days         = 7
  geo_redundant_backup_enabled  = false
  public_network_access_enabled = true # simplest; locked down by the firewall rule below
  administrator_login           = "album"
  administrator_password        = random_password.postgres.result
  tags                          = var.tags

  lifecycle {
    # Azure picks an availability zone; don't fight it on later plans
    ignore_changes = [zone]
  }
}

resource "azurerm_postgresql_flexible_server_database" "album" {
  name      = "album"
  server_id = azurerm_postgresql_flexible_server.main.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

# 0.0.0.0 - 0.0.0.0 is Azure's special "allow Azure services" rule, which lets AKS in.
# (Tighter: allow only the cluster's outbound IP, or use private networking - costs more.)
resource "azurerm_postgresql_flexible_server_firewall_rule" "azure_services" {
  name             = "allow-azure-services"
  server_id        = azurerm_postgresql_flexible_server.main.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

# ---------- Budget alert: email before the bill surprises you ----------
resource "azurerm_consumption_budget_resource_group" "main" {
  name              = "budget-${local.name}"
  resource_group_id = azurerm_resource_group.main.id
  amount            = var.budget_amount
  time_grain        = "Monthly"

  time_period {
    # Budgets must start on the first day of a month
    start_date = formatdate("YYYY-MM-01'T'00:00:00Z", timestamp())
  }

  notification {
    enabled        = true
    threshold      = 50
    operator       = "GreaterThanOrEqualTo"
    threshold_type = "Actual"
    contact_emails = [var.budget_email]
  }

  notification {
    enabled        = true
    threshold      = 80
    operator       = "GreaterThanOrEqualTo"
    threshold_type = "Actual"
    contact_emails = [var.budget_email]
  }

  notification {
    enabled        = true
    threshold      = 100
    operator       = "GreaterThanOrEqualTo"
    threshold_type = "Forecasted"
    contact_emails = [var.budget_email]
  }

  lifecycle {
    # timestamp() changes every run; the start date only matters on creation
    ignore_changes = [time_period]
  }
}
