output "resource_group" {
  value = azurerm_resource_group.main.name
}

output "aks_get_credentials" {
  description = "Run this to point kubectl at the new cluster."
  value       = "az aks get-credentials --resource-group ${azurerm_resource_group.main.name} --name ${azurerm_kubernetes_cluster.main.name}"
}

output "storage_account_url" {
  description = "AZURE_STORAGE_ACCOUNT_URL for k8s/overlays/azure/kustomization.yaml"
  value       = azurerm_storage_account.photos.primary_blob_endpoint
}

output "workload_identity_client_id" {
  description = "Client ID for the ServiceAccount annotation in k8s/overlays/azure/serviceaccount.yaml"
  value       = azurerm_user_assigned_identity.app.client_id
}

output "postgres_fqdn" {
  value = azurerm_postgresql_flexible_server.main.fqdn
}

output "database_url" {
  description = "DATABASE_URL for the event-album-secrets Secret. Show with: terraform output -raw database_url"
  value       = "postgresql://album:${random_password.postgres.result}@${azurerm_postgresql_flexible_server.main.fqdn}:5432/album?sslmode=require"
  sensitive   = true
}
