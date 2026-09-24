# SKETCH: reviewed, not applied. `terraform plan` against a real project is the
# next step and requires the customer's GCP project and deployment rights.
terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
  # backend "gcs" { bucket = "<state-bucket>"  prefix = "artifact-hub" }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
