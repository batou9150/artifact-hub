# ── Optional: Cloud Storage for bodies above the Firestore cap ───────────
resource "google_storage_bucket" "bodies" {
  count                       = var.enable_gcs_bodies ? 1 : 0
  name                        = "${var.project_id}-${local.name}-bodies"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  versioning {
    enabled = true
  }
}

resource "google_storage_bucket_iam_member" "runtime_bodies" {
  count  = var.enable_gcs_bodies ? 1 : 0
  bucket = google_storage_bucket.bodies[0].name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.runtime.email}"
}

# ── Optional: artifact_* events to BigQuery (usage dashboard) ─────────────
resource "google_bigquery_dataset" "events" {
  count      = var.enable_event_sink ? 1 : 0
  dataset_id = replace("${local.name}_events", "-", "_")
  location   = "EU"
}

resource "google_logging_project_sink" "events" {
  count                  = var.enable_event_sink ? 1 : 0
  name                   = "${local.name}-events"
  destination            = "bigquery.googleapis.com/projects/${var.project_id}/datasets/${google_bigquery_dataset.events[0].dataset_id}"
  filter                 = "resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"${local.name}\" AND jsonPayload.event=~\"^artifact_\""
  unique_writer_identity = true
  bigquery_options {
    use_partitioned_tables = true
  }
}

resource "google_bigquery_dataset_iam_member" "sink_writer" {
  count      = var.enable_event_sink ? 1 : 0
  dataset_id = google_bigquery_dataset.events[0].dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = google_logging_project_sink.events[0].writer_identity
}

# ── Optional: GitHub Actions deploys through Workload Identity Federation ──
resource "google_iam_workload_identity_pool" "github" {
  count                     = var.github_repository == "" ? 0 : 1
  workload_identity_pool_id = "${local.name}-gh"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  count                              = var.github_repository == "" ? 0 : 1
  workload_identity_pool_id          = google_iam_workload_identity_pool.github[0].workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }
  attribute_condition = "assertion.repository == \"${var.github_repository}\""
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "deployer" {
  count      = var.github_repository == "" ? 0 : 1
  account_id = "${local.name}-deploy"
}

resource "google_service_account_iam_member" "deployer_wif" {
  count              = var.github_repository == "" ? 0 : 1
  service_account_id = google_service_account.deployer[0].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github[0].name}/attribute.repository/${var.github_repository}"
}

resource "google_project_iam_member" "deployer_roles" {
  for_each = var.github_repository == "" ? toset([]) : toset(["roles/run.developer", "roles/artifactregistry.writer"])
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.deployer[0].email}"
}

resource "google_service_account_iam_member" "deployer_acts_as_runtime" {
  count              = var.github_repository == "" ? 0 : 1
  service_account_id = google_service_account.runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deployer[0].email}"
}
