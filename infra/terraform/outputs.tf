output "service_url" { value = google_cloud_run_v2_service.hub.uri }
output "runtime_service_account" { value = google_service_account.runtime.email }
output "image_repository" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}
output "wif_provider" {
  value = var.github_repository == "" ? null : google_iam_workload_identity_pool_provider.github[0].name
}
output "deployer_service_account" {
  value = var.github_repository == "" ? null : google_service_account.deployer[0].email
}
