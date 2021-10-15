# gcp-quota-check

Show Google Cloud Compute Engine quotas close to their limits across regions, from gcloud output, with CI-friendly exit codes.

## Planned scope

Offline on `gcloud compute regions list --format=json` / `regions describe` and `project-info describe` (both include quotas: metric, limit, usage). Compute % used, thresholds, sort, filter by metric/region, table/JSON/CSV, exit codes; note that GPU and some quotas show limit 0 in many projects and treat limit 0 sensibly. Optional live mode via an injectable client for the Compute API is not required; keep it offline.
