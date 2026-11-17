terraform {
  required_version = ">= 1.6.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.81.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.9.0"
    }
  }

  # No backend block = local backend: state is kept in ./terraform.tfstate on your PC
  # (gitignored - it contains the database password). Fine for a solo learning project.
  # For a team you would move it to an Azure Storage "azurerm" backend.
}

provider "azurerm" {
  features {
    resource_group {
      # Let `terraform destroy` remove the group even if something extra was created in it
      prevent_deletion_if_contains_resources = false
    }
  }
  # null = use the ARM_SUBSCRIPTION_ID env var or the `az login` default subscription
  subscription_id = var.subscription_id
}
