# gcp-quota-check

Show which Google Cloud Compute Engine quotas are close to their limits, across regions and project-wide, from the JSON that gcloud already prints. Exit codes make it usable as a CI gate or a cron check.

**Status: working first version (0.1.0).** Reading `regions list`, `regions describe` and `project-info describe` output, percentage thresholds, metric and region filters, sorting, table/JSON/CSV output and exit codes all work. The tool reads files only; it never calls Google Cloud. The example data is hand-written to match the documented `quotas` fields and has not been compared against a real export by the author yet.

## Install

```bash
pip install git+https://github.com/narayana006/gcp-quota-check
```

Python 3.10 or newer, no runtime dependencies.

## Quick start

The `examples/` files are illustrative, hand-written exports for a fictional project `acme-prod-1234`, in the shape of `gcloud compute regions list --format=json` and `gcloud compute project-info describe --format=json`:

```console
$ gcp-quota-check examples/regions.json examples/project.json
Checked 105 quota(s) (3 region(s), project-wide) for acme-prod-1234; warn at 80%, critical at 95%

SCOPE        METRIC            USAGE  LIMIT    USED  STATUS
us-central1  N2_CPUS              24     24  100.0%  CRITICAL
us-central1  CPUS                 70     72   97.2%  CRITICAL
global       CPUS_ALL_REGIONS     94    100   94.0%  WARNING
global       NETWORKS             14     15   93.3%  WARNING
global       URL_MAPS              9     10   90.0%  WARNING
us-central1  STATIC_ADDRESSES      7      8   87.5%  WARNING
us-central1  SSD_TOTAL_GB       1750   2048   85.4%  WARNING
global       FIREWALLS           163    200   81.5%  WARNING

Summary: 0 over limit, 2 critical, 6 warning, 78 ok, 19 with limit 0 (hidden; --include-zero to list)
$ echo $?
2
```

`global` rows come from `project-info describe` (project-wide quotas); the others come from the region they belong to.

## Use it on your project

```bash
gcloud compute regions list --format=json > regions.json
gcloud compute project-info describe --format=json > project.json
gcp-quota-check regions.json project.json
```

A single region works too (`gcloud compute regions describe us-central1 --format=json`), and `-` reads from stdin:

```bash
gcloud compute regions describe us-central1 --format=json | gcp-quota-check - --metric 'CPUS*'
```

Add `--project=PROJECT_ID` to the gcloud commands for a project other than your default. Each file may hold one object or a list of them; files are combined.

| Option | Meaning |
| --- | --- |
| `--warn PCT` | Warning threshold in percent of the limit (default 80). |
| `--critical PCT` | Critical threshold (default 95). |
| `--metric PATTERN` | Only matching metrics. Shell wildcards, case-insensitive, repeatable: `--metric CPUS --metric '*GPUS'`. |
| `--exclude-metric PATTERN` | Skip matching metrics. |
| `--region PATTERN` | Only matching regions; `global` selects the project-wide quotas. Repeatable, wildcards allowed. |
| `--all` | List every quota, not only those at or above `--warn`. |
| `--include-zero` | Also list quotas whose limit is 0. |
| `--sort percent\|metric\|scope` | `percent` (default) lists the worst first. |
| `--top N` | At most N rows. |
| `--format table\|json\|csv` | Output format (default `table`). |

### Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Every checked quota is below `--warn` (or has limit 0 and no usage). |
| 1 | At least one quota is at or above `--warn`. |
| 2 | At least one quota is at or above `--critical`, or usage is above the limit. |
| 3 | Bad arguments or unreadable / unrecognised input. |

Filters decide what is checked, so the exit code only reflects the quotas you selected. For example, to fail a pipeline only on CPU quotas in two regions:

```bash
gcp-quota-check regions.json --metric '*CPUS' --region us-central1 --region us-east1 --warn 70 --critical 90
```

### Quotas with limit 0

Many projects have a limit of 0 for GPU, preemptible and committed quotas until someone requests more. These are not "100% used", so they are hidden by default, counted in the summary, and never change the exit code. Use `--include-zero` to see them, which is useful before launching GPU work:

```console
$ gcp-quota-check examples/regions.json --metric '*GPUS' --region us-central1 --include-zero
Checked 4 quota(s) (1 region(s)) for acme-prod-1234; warn at 80%, critical at 95%

SCOPE        METRIC                      USAGE  LIMIT  USED  STATUS
us-central1  NVIDIA_A100_GPUS                0      0     -  ZERO-LIMIT
us-central1  NVIDIA_L4_GPUS                  0      0     -  ZERO-LIMIT
us-central1  NVIDIA_T4_GPUS                  0      0     -  ZERO-LIMIT
us-central1  PREEMPTIBLE_NVIDIA_T4_GPUS      0      0     -  ZERO-LIMIT

Summary: 0 over limit, 0 critical, 0 warning, 0 ok, 4 with limit 0
```

If usage is above 0 under a limit of 0, that row is reported as `OVER`. A negative limit is treated as "no limit" and never alerts.

### CSV and JSON

```console
$ gcp-quota-check examples/regions.json examples/project.json --format csv --metric 'CPUS*' --all
project,scope,metric,usage,limit,percent,status
acme-prod-1234,us-central1,CPUS,70,72,97.2,critical
acme-prod-1234,global,CPUS_ALL_REGIONS,94,100,94.0,warning
acme-prod-1234,us-east1,CPUS,16,24,66.7,ok
acme-prod-1234,europe-west1,CPUS,8,24,33.3,ok
```

`--format json` prints `projects`, `regions`, `thresholds`, `summary` (counts per status), `warnings` and `quotas` (one object per shown row with `scope`, `project`, `metric`, `limit`, `usage`, `percent`, `status`).

If a region object carries a `quotaStatusWarning` (Compute Engine sets it when the quota data for that region could not be fetched completely), the tool prints a warning on stderr and includes it under `warnings`.

## Permissions

The tool needs no credentials. The gcloud commands that produce its input need `compute.regions.list` (or `compute.regions.get` for `describe`) and `compute.projects.get`, all included in `roles/compute.viewer`.

## Limitations

- Only the Compute Engine quotas that these gcloud commands return. Quotas of other services (and any Compute Engine quota not included in the `quotas` array) are not seen; the Cloud console Quotas page remains the full view.
- It reads a point-in-time export; it does not track trends or forecast when a quota will run out.
- Thresholds are the same for every metric; per-metric thresholds are not supported yet.
- No live mode: it reads files by design.

## Feature checklist

- [x] Read `regions list`, `regions describe` and `project-info describe` JSON (files or stdin)
- [x] Percent used with warning and critical thresholds
- [x] Sensible handling of limit 0 and usage over the limit
- [x] Metric and region filters with wildcards, sorting, `--top`
- [x] Table, JSON and CSV output
- [x] CI-friendly exit codes (0/1/2, and 3 for bad input)
- [ ] Per-metric thresholds from a config file
- [ ] Optional live read through the Compute API

## Contributing

Issues and pull requests are welcome. To run the tests:

```bash
pip install -e ".[dev]"
pytest
```

A sanitised real export that the tool misreads is the most useful bug report.

Built and maintained by [Narayana Vemburaj](https://github.com/narayana006).

Licensed under the [MIT License](LICENSE).
