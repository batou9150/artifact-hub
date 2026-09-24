locals {
  name = "artifact-hub-${var.environment}"
  apis = [
    "run.googleapis.com",
    "firestore.googleapis.com",
    "secretmanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "aiplatform.googleapis.com",
    "iamcredentials.googleapis.com",
  ]
}

resource "google_project_service" "apis" {
  for_each           = toset(local.apis)
  service            = each.value
  disable_on_destroy = false
}

# ── container registry ────────────────────────────────────────────────────
resource "google_artifact_registry_repository" "images" {
  repository_id = "artifact-hub"
  location      = var.region
  format        = "DOCKER"
  depends_on    = [google_project_service.apis]
}

# ── runtime identity (least privilege) ───────────────────────────────────
resource "google_service_account" "runtime" {
  account_id   = "${local.name}-run"
  display_name = "Artifact Hub runtime (${var.environment})"
}

resource "google_project_iam_member" "runtime_firestore" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_project_iam_member" "runtime_vertex" {
  count   = var.embedding_provider == "vertex" ? 1 : 0
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

# ── Firestore (Native mode) ──────────────────────────────────────────────
resource "google_firestore_database" "db" {
  name                    = var.environment == "prod" ? "(default)" : local.name
  location_id             = var.region
  type                    = "FIRESTORE_NATIVE"
  delete_protection_state = var.environment == "prod" ? "DELETE_PROTECTION_ENABLED" : "DELETE_PROTECTION_DISABLED"
  depends_on              = [google_project_service.apis]
}

resource "google_firestore_field" "no_index_version_body" {
  database   = google_firestore_database.db.name
  collection = "versions"
  field      = "body"
  index_config {}
}

resource "google_firestore_field" "no_index_embedding" {
  database   = google_firestore_database.db.name
  collection = "artifacts"
  field      = "embedding"
  index_config {}
}

# ── render-ticket HMAC key ───────────────────────────────────────────────
resource "random_password" "render_ticket" {
  length  = 48
  special = false
}

resource "google_secret_manager_secret" "render_ticket" {
  secret_id = "${local.name}-render-ticket"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "render_ticket" {
  secret      = google_secret_manager_secret.render_ticket.id
  secret_data = random_password.render_ticket.result
}

resource "google_secret_manager_secret_iam_member" "runtime_reads_ticket_key" {
  secret_id = google_secret_manager_secret.render_ticket.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

# ── Cloud Run: API + sandbox (/a/*) + MCP (/mcp) + SPA, one service ───────
resource "google_cloud_run_v2_service" "hub" {
  name     = local.name
  location = var.region
  # Public ingress: authentication is enforced by the application (OIDC bearer
  # tokens on /api and /mcp, render tickets on /a/*), not by Cloud Run IAM,
  # because MCP clients call from outside the organisation. Put Cloud Armor in
  # front through a load balancer if an IP allow-list is required.
  ingress = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.runtime.email
    scaling {
      min_instance_count = 0
      max_instance_count = 5
    }
    containers {
      image = var.image
      resources {
        limits = { cpu = "1", memory = "512Mi" }
      }
      env {
        name  = "ENVIRONMENT"
        value = var.environment
      }
      env {
        name  = "AUTH_MODE"
        value = "oidc"
      }
      env {
        name  = "PUBLIC_BASE_URL"
        value = var.public_base_url
      }
      env {
        name  = "APP_ORIGIN"
        value = var.public_base_url
      }
      env {
        name  = "CORS_ORIGINS"
        value = ""
      }
      env {
        name  = "OIDC_ISSUER"
        value = var.oidc_issuer
      }
      env {
        name  = "OIDC_AUDIENCES"
        value = join(",", var.oidc_audiences)
      }
      env {
        name  = "OIDC_CLIENT_ID"
        value = var.oidc_client_id
      }
      env {
        name  = "OIDC_SCOPES"
        value = var.oidc_scopes
      }
      env {
        name  = "OIDC_UI_TOKEN"
        value = var.oidc_ui_token
      }
      env {
        name  = "OIDC_GROUPS_CLAIM"
        value = var.oidc_groups_claim
      }
      env {
        name  = "PUBLISHER_GROUP"
        value = var.publisher_group
      }
      env {
        name  = "ADMIN_GROUP"
        value = var.admin_group
      }
      env {
        name  = "ALLOWED_EMAIL_DOMAINS"
        value = join(",", var.allowed_email_domains)
      }
      env {
        name  = "STORE_BACKEND"
        value = "firestore"
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "FIRESTORE_DATABASE"
        value = google_firestore_database.db.name
      }
      env {
        name  = "EMBEDDING_PROVIDER"
        value = var.embedding_provider
      }
      env {
        name  = "VERTEX_LOCATION"
        value = var.region
      }
      env {
        name = "RENDER_TICKET_SECRET"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.render_ticket.secret_id
            version = "latest"
          }
        }
      }
      startup_probe {
        http_get {
          path = "/healthz"
        }
      }
    }
  }
  depends_on = [google_secret_manager_secret_iam_member.runtime_reads_ticket_key]
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  name     = google_cloud_run_v2_service.hub.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}
