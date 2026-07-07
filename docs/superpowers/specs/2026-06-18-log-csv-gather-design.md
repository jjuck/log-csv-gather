# CSV Log Gather Design

## Summary

CSV Log Gather moves daily CSV log files between field PCs, Google Drive, and management PCs.

The system has two roles:

- Field PC uploader: scans a local log root such as `E:\`, selects only daily `总数据` CSV files, uploads them to Google Drive, and records upload completion locally.
- Management PC downloader: scans Google Drive, downloads files that the current management PC has not downloaded, and records download completion locally.

Upload completion and download completion are separate states. A file can be uploaded by a field PC while still not downloaded by one or more management PCs.

The production baseline uses:

- Python CLI package
- local-only FastAPI dashboard
- Google Drive API with OAuth
- SQLite per PC
- Windows Task Scheduler for the hourly loop

## Goals

- Upload one summary CSV per date and log type from each field PC.
- Download uploaded CSV files to management PCs using the normalized folder structure.
- Ignore local `fail` folders and non-summary CSV files.
- Preserve idempotency across repeated hourly runs.
- Track failures, conflicts, and retry attempts per PC.
- Publish lightweight status JSON files to Google Drive.
- Let non-programmer operators configure and observe the app through `run.bat` and the local dashboard.

## Non-Goals

- Do not parse CSV contents.
- Do not use n8n for the core upload/download flow.
- Do not require near-real-time file watching.
- Do not upload every CSV file; only `总数据` summary CSV files are in scope.
- Do not auto-resolve conflicts.
- Do not expose service account setup in the operator UX yet.
- Do not use WebSocket in the current dashboard; REST polling is sufficient for v1.

## Local Source Layout

Field PCs have a log root equivalent to `E:\`.

Expected source layout:

```text
E:\
  fail\                         # ignored
  PAS Test data\
    20260401\
      20260401_MIC1数据.csv      # ignored
      20260401_MIC2数据.csv      # ignored
      20260401_总数据.csv        # uploaded
  HM-3203-011 Test data\
    20260401\
      20260401_总数据.csv        # uploaded
  HM-3903-011 Test data\
    20260401\
      20260401_总数据.csv        # uploaded
  LITE Test data\
    20260401\
      20260401_总数据.csv        # uploaded
  SMIC_Test data\
    20260401\
      20260401_总数据.csv        # uploaded
```

Only these five source folders are processed:

| Source folder | Normalized log type |
| --- | --- |
| `PAS Test data` | `PAS` |
| `HM-3203-011 Test data` | `3203` |
| `HM-3903-011 Test data` | `3903` |
| `LITE Test data` | `LITE` |
| `SMIC_Test data` | `SMIC` |

The uploader ignores:

- `fail`
- unmapped folders
- non-CSV files
- CSV files that do not contain `总数据` in the file name
- date folders not named exactly `YYYYMMDD`

## Google Drive Layout

Google Drive has one configured root folder. The application stores that root as a Drive folder ID in config.

Normalized upload path:

```text
logs/{group_name}/{log_type}/{machine_id}/{YYMMDD}/{YYMMDD}_{log_type}.csv
```

Example:

```text
logs/Array_MIC/PAS/성능검사기_1/260401/260401_PAS.csv
```

Status files:

```text
status/
  uploaders/
    {pc_id}.json
  downloaders/
    {pc_id}.json
  doctor/
    {pc_id}.json
```

## Management PC Download Layout

Management PCs download files into a configured local root using the same normalized path below `logs`.

Example:

```text
download_root\
  Array_MIC\
    PAS\
      성능검사기_1\
        260401\
          260401_PAS.csv
```

## Commands

```text
python -m log_csv_gather auth --config config.yaml
python -m log_csv_gather doctor --config config.yaml
python -m log_csv_gather upload --config config.yaml
python -m log_csv_gather upload --config config.yaml --dry-run
python -m log_csv_gather download --config config.yaml
python -m log_csv_gather download --config config.yaml --dry-run
python -m log_csv_gather status --config config.yaml
python -m log_csv_gather status --config config.yaml --details
python -m log_csv_gather web --config config.yaml
```

Operators normally use only:

```text
run.bat
```

## Active Config Model

`configs/active.yaml` is the mutable per-PC config.

`configs/production.uploader.yaml` and `configs/production.downloader.yaml` are templates and recovery sources. The dashboard Setup dialog edits only `active.yaml`.

Setup exposes:

- role
- PC name (`pc_id`)
- Drive root folder ID
- uploader machine name (`machine_id`)
- uploader log root (`source_root`)
- downloader destination (`download_root`)

Setup does not expose OAuth client secret path, token path, or service account fields.

Saving setup:

- validates and writes `active.yaml`
- allows missing or partial log folders but reports warnings
- does not run Doctor automatically
- does not register the scheduler automatically
- unregisters an existing scheduled task if current settings changed while a task was registered
- marks Doctor as verification-needed in the UI

## Local Folder Browser

The dashboard provides a local folder browser using FastAPI because the Python server runs on the same PC as the browser.

Rules:

- list local and mapped drives
- list folders only
- hide hidden/system folders by default
- handle access denied without crashing
- allow direct path typing as a fallback
- validate immediately after folder selection

Uploader validation reports how many of the four mapped source folders exist.

Downloader validation reports whether the selected destination exists or can be created and written.

Warnings do not block saving because test PCs and partially initialized field PCs may not have all folders yet.

## Local Web Dashboard

The dashboard is local-only:

- `127.0.0.1` only
- preferred port `8765`
- sequential port fallback
- browser-open throttle to avoid duplicate windows
- no remote access
- REST polling instead of WebSocket

Important endpoints:

```text
GET  /api/health
POST /api/actions/{action}
GET  /api/jobs/{job_id}
GET  /api/feed
GET  /api/status?details=true
GET  /api/logs/tail
GET  /api/scheduler
POST /api/scheduler/register
POST /api/scheduler/enable
POST /api/scheduler/disable
POST /api/scheduler/unregister
GET  /api/config/active
POST /api/config/role
POST /api/config/active/reset
GET  /api/local/drives
GET  /api/local/folders
POST /api/local/validate-path
POST /api/config/setup
```

## Upload Flow

For each uploader run:

1. Load config and authenticate with Google Drive.
2. Open or initialize local SQLite state.
3. Scan only mapped source folders below `source_root`.
4. Select `*总数据*.csv` under `YYYYMMDD` date folders.
5. Skip files modified within `file_stable_minutes`.
6. Convert `YYYYMMDD` to `YYMMDD`.
7. Build the normalized Drive path.
8. Skip already-uploaded matching records.
9. If Drive already has same size and MD5, record success.
10. If Drive already has different content, record conflict.
11. Upload missing files.
12. Record success or failure in SQLite.
13. Write status JSON to Drive.

The uploader never moves, renames, or deletes source files.

## Download Flow

For each downloader run:

1. Load config and authenticate with Google Drive.
2. Open or initialize local SQLite state.
3. List files under `logs/`.
4. Apply optional group, log type, and machine filters.
5. Build the local destination path.
6. Skip already-downloaded matching records.
7. If the local file already exists with same size and MD5, record success.
8. If the local file already exists with different content, record conflict.
9. Download missing files.
10. Record success or failure in SQLite.
11. Write status JSON to Drive.

## Local State Database

Each PC owns its local SQLite database. SQLite is the source of truth for that PC's upload/download history.

Upload statuses:

- `pending`
- `uploaded`
- `failed`
- `conflict`

Download statuses:

- `pending`
- `downloaded`
- `failed`
- `conflict`

`failed` is retryable. `conflict` is not retried automatically.

## Retry And Failure Classification

Retryable network failures:

- `TimeoutError`
- connection reset/refused
- DNS transient failure
- HTTP 500/502/503/504
- rate limit after client retries

Retryable failures should continue to the next file. At the end of the job, failed items wait 10 seconds and retry once. Remaining failed items retry on the next scheduler run or manual Once.

Structural failures stop early:

- OAuth token missing/invalid/expired without refresh
- Drive root folder ID invalid or inaccessible
- insufficient Drive permissions
- Google Drive API/project configuration errors
- Drive storage quota errors
- uploader source root inaccessible
- downloader destination cannot be created or written

## Observability UX

Button state is based on the last run of that action. System Status is based on current aggregate state.

Color rules:

- gray: never run
- blue: running
- green: success
- yellow: retryable failed items
- amber: conflict exists
- red: structural failure, Auth failure, or Doctor failure

Current Job owns progress bars and uses structured progress payloads:

- Upload/Download/Dry-run: current file over total candidates.
- Doctor/Auth: step-based progress.
- Retry pass: separate phase after first-pass processing.

Feed shows major events only and is displayed newest-first. Per-file details live in `app.log`.

## Authentication

The current operator flow uses installed-app OAuth.

- Auth opens or instructs the user to complete Google's OAuth flow.
- Each PC stores its own token under `state_dir`.
- The same Google account or shared Drive folder is used by all PCs.
- Drive root folder ID controls where files are created and read.

Service account support can remain in lower-level config/code, but service account setup is not part of the current operator UX.

## Logging

Each PC writes UTF-8 logs under:

```text
{state_dir}/logs/app.log
```

Logs should include:

- run start and end
- role and PC ID
- selected file count
- uploaded/downloaded/skipped/failed/conflict counts
- per-file errors
- Drive/Auth errors

Logs must not include OAuth secrets or token contents.

## Testing Strategy

Automated tests:

- config loading and setup saving
- local folder browser APIs
- source folder to log type mapping
- selection of only `*总数据*.csv`
- ignoring `fail`, unknown folders, and invalid date folders
- normalized Drive paths
- SQLite idempotency
- conflict classification
- fake Drive upload/download workflows
- web health/status/job/scheduler/setup APIs

Manual verification:

- sample `E:\`-like fixture uploads exactly expected files
- downloader recreates `Array_MIC/{log_type}/{machine_id}/{YYMMDD}/{YYMMDD}_{log_type}.csv`
- dashboard setup can select folders and save active config
- Auth, Doctor, Dry-run, Once, and Scheduler can be operated from the dashboard

## Implementation Boundaries

Keep modules separate:

- config loading and YAML updates
- active config management
- local folder browser and path validation
- source scanning
- normalized path mapping
- Google Drive adapter
- SQLite state repository
- upload/download workflows
- status JSON writer
- scheduler adapter
- CLI entry point
- local web dashboard
