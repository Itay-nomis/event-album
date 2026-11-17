variable "subscription_id" {
  description = "Azure subscription ID. Leave null to use `az account show` / ARM_SUBSCRIPTION_ID."
  type        = string
  default     = null
}

variable "location" {
  description = "Azure region."
  type        = string
  default     = "westeurope"
}

variable "prefix" {
  description = "Short lowercase name used in every resource name."
  type        = string
  default     = "evalbum"

  validation {
    condition     = can(regex("^[a-z0-9]{3,12}$", var.prefix))
    error_message = "prefix must be 3-12 lowercase letters/digits (it is part of the storage account name)."
  }
}

variable "aks_node_size" {
  description = "VM size of the single AKS node. AKS system pools need at least 2 vCPU / 4 GB."
  type        = string
  default     = "Standard_B2s"
}

variable "postgres_sku" {
  description = "Smallest burstable Postgres Flexible Server SKU."
  type        = string
  default     = "B_Standard_B1ms"
}

variable "budget_amount" {
  description = "Monthly budget for the resource group, in your billing currency."
  type        = number
  default     = 20
}

variable "budget_email" {
  description = "Email address that receives the budget alerts (required)."
  type        = string
}

variable "tags" {
  description = "Tags added to every resource."
  type        = map(string)
  default     = { project = "event-album", purpose = "learning" }
}
