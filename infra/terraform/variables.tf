variable "project_id" { type = string }
variable "region" {
  type    = string
  default = "europe-west1"
}
variable "environment" {
  type        = string
  description = "dev or prod"
  default     = "dev"
}
variable "image" {
  type        = string
  description = "Container image (Artifact Registry) built by the CI workflow."
}
variable "public_base_url" {
  type        = string
  description = "https://<host> the service answers on (custom domain or the run.app URL)."
}

# OIDC (Entra ID / Okta / Google)
variable "oidc_issuer" { type = string }
variable "oidc_audiences" {
  type        = list(string)
  description = "Accepted token audiences (API app id URI for Entra ID, authorization server audience for Okta, client id for Google ID tokens)."
}
variable "oidc_client_id" {
  type        = string
  description = "Public SPA client (auth code + PKCE, no secret)."
}
variable "oidc_scopes" {
  type    = string
  default = "openid profile email"
}
variable "oidc_ui_token" {
  type    = string
  default = "access"
}
variable "oidc_groups_claim" {
  type    = string
  default = "groups"
}
variable "publisher_group" {
  type        = string
  description = "Group (name or object id as emitted in the groups claim) allowed to share org-wide."
}
variable "admin_group" {
  type        = string
  description = "Group allowed to delete any artifact."
}
variable "allowed_email_domains" {
  type    = list(string)
  default = []
}

variable "embedding_provider" {
  type    = string
  default = "vertex"
}

variable "enable_gcs_bodies" {
  type        = bool
  default     = false
  description = "Create a private bucket for artifact bodies above the Firestore cap (application support is a follow-up, see docs/open-points.md)."
}

variable "enable_event_sink" {
  type        = bool
  default     = false
  description = "Route artifact_* events from Cloud Logging to BigQuery for the usage dashboard."
}

variable "github_repository" {
  type        = string
  default     = ""
  description = "owner/repo allowed to deploy through Workload Identity Federation (empty = skip)."
}
