# CSV Log Gather Design

## Summary

Build a Python command-line program that moves daily CSV log files between field PCs, Google Drive, and management PCs.

The system has two operational roles:

- Field PCs run an uploader every hour. The uploader scans a local log root such as `E:\`, selects only daily `总数据` CSV files, uploads them to Google Drive, and records upload completion locally.
- Management PCs run a downloader every hour or on demand. The downloader scans Google Drive, downloads files that the current management PC has not downloaded yet, and records download completion locally.

Upload completion and download completion are separate states. A file can be uploaded by a field PC while still not downloaded by one or more management PCs.

The first implementation will use Windows Task Scheduler for hourly execution, Google Drive API for cloud storage, and SQLite for local state on each PC.

## Goals

- Upload one summary CSV per date and log type from each field PC.
- Download uploaded CSV files to management PCs using the same normalized folder structure.
- Ignore local `fail` folders and non-summary CSV files such as `MIC1数据` and `MIC2数据`.
- Preserve idempotency: repeated hourly runs must not create duplicate Drive files or duplicate local downloads.
- Track failures and retries per PC.
- Publish lightweight status JSON files to Google Drive so n8n or another monitor can alert on stale PCs or repeated failures.

## Non-Goals

- Do not parse CSV contents in the first version.
- Do not build a GUI in the first version.
- Do not use n8n for the core upload/download flow in the first version.
- Do not require near-real-time file watching. Hourly execution is acceptable.
- Do not upload every CSV file. Only `总数据` summary files are in scope.

## Local Source Layout

Field PCs will have a log root equivalent to `E:\`.

Expected source layout:

```text
E:\
  fail\                         # ignored
  PAS Test data\
    20260401\
      20260401_MIC1数据.csv     # ignored
      20260401_MIC2数据.csv     # ignored
      20260401_总数据.csv       # uploaded
  HM-3203-011 Test data\
    20260401\
      20260401_总数据.csv       # uploaded
  HM-3903-011 Test data\
    20260401\
      20260401_总数据.csv       # uploaded
  LITE Test data\
    20260401\
      20260401_总数据.csv       # uploaded
```

Only these four source folders are processed:

| Source folder | Normalized log type |
| --- | --- |
| `PAS Test data` | `PAS` |
| `HM-3203-011 Test data` | `3203` |
| `HM-3903-011 Test data` | `3903` |
| `LITE Test data` | `LITE` |

The uploader ignores:

- `fail`
- folders not listed in the mapping
- files that are not `.csv`
- CSV files that do not contain `总数据` in the file name
- date folders that are not exactly `YYYYMMDD`

## Google Drive Layout

Google Drive has one configured root folder. The application stores that root as a Drive folder ID in config and does not rely only on a display name.

Normalized upload path:

```text
logs/{group_name}/{log_type}/{machine_id}/{YYMMDD}/{YYMMDD}_{log_type}.csv
```

Example:

```text
logs/Array_MIC/PAS/성능검사기_1/260401/260401_PAS.csv
```

Mapping example:

```text
E:\PAS Test data\20260401\20260401_总数据.csv
-> logs/Array_MIC/PAS/성능검사기_1/260401/260401_PAS.csv
```

Other examples:

```text
logs/Array_MIC/3203/성능검사기_1/260401/260401_3203.csv
logs/Array_MIC/3903/성능검사기_1/260401/260401_3903.csv
logs/Array_MIC/LITE/성능검사기_1/260401/260401_LITE.csv
```

Status files are stored separately:

```text
status/
  uploaders/
    {pc_id}.json
  downloaders/
    {pc_id}.json
```

## Management PC Download Layout

Management PCs download files into a configured local root using the same normalized path under `logs`.

Example:

```text
download_root\
  Array_MIC\
    PAS\
      성능검사기_1\
        260401\
          260401_PAS.csv
```

By default, a downloader downloads all configured groups, log types, machines, and dates that it has not already downloaded. Optional config filters can restrict this by group, log type, machine, or date range.

## Command Modes

The same codebase provides separate commands:

```text
python -m log_csv_gather upload --config config.yaml
python -m log_csv_gather download --config config.yaml
python -m log_csv_gather status --config config.yaml
python -m log_csv_gather auth --config config.yaml
```

Windows Task Scheduler runs `upload` on field PCs and `download` on management PCs every hour.

Management users can run the same `download` command manually when they want an immediate sync.

## Configuration

Uploader config example:

```yaml
role: uploader
pc_id: field-pc-01
group_name: Array_MIC
machine_id: 성능검사기_1
drive_root_folder_id: google-drive-folder-id
source_root: "E:\\"
state_dir: "C:\\log-csv-gather\\state"
file_stable_minutes: 5
log_type_mappings:
  "PAS Test data": "PAS"
  "HM-3203-011 Test data": "3203"
  "HM-3903-011 Test data": "3903"
  "LITE Test data": "LITE"
ignore_dirs:
  - fail
```

Downloader config example:

```yaml
role: downloader
pc_id: management-pc-01
drive_root_folder_id: google-drive-folder-id
download_root: "D:\\downloaded-logs"
state_dir: "C:\\log-csv-gather\\state"
include_groups:
  - Array_MIC
include_log_types:
  - PAS
  - "3203"
  - "3903"
  - LITE
```

## Upload Flow

For each hourly uploader run:

1. Load config and authenticate with Google Drive.
2. Open or initialize local SQLite state.
3. Scan only mapped source folders under `source_root`.
4. In each `YYYYMMDD` date folder, select only `*总数据*.csv`.
5. Skip files modified within the last `file_stable_minutes`.
6. Convert date from `YYYYMMDD` to `YYMMDD`.
7. Build the normalized Drive path.
8. Check local SQLite for an already uploaded record with the same Drive path and file fingerprint.
9. If not already uploaded, ensure Drive folders exist and upload the file.
10. If a Drive file already exists at the target path:
    - if size and hash match, record success;
    - if they differ, record a conflict and do not overwrite.
11. Record success or failure in SQLite.
12. Write a status JSON file to Drive.

The uploader should not move, rename, or delete original source files.

## Download Flow

For each hourly or manual downloader run:

1. Load config and authenticate with Google Drive.
2. Open or initialize local SQLite state.
3. List files under `logs/` in the configured Drive root.
4. Apply configured filters for group, log type, machine, and date if present.
5. Build the matching local destination path.
6. Check local SQLite for an already downloaded record with the same Drive file ID and fingerprint.
7. If the local file already exists:
    - if size and hash match, record success;
    - if they differ, record a conflict and do not overwrite.
8. Download missing files.
9. Record success or failure in SQLite.
10. Write a status JSON file to Drive.

## Local State Database

Each PC owns its local SQLite database. The database is the source of truth for that PC's upload or download history.

Upload record fields:

```text
id
source_path
drive_file_id
drive_path
group_name
log_type
machine_id
source_date_yyyymmdd
target_date_yymmdd
source_size
source_mtime
content_hash
status              # pending, uploaded, failed, conflict
attempt_count
uploaded_at
last_attempt_at
last_error
```

Download record fields:

```text
id
drive_file_id
drive_path
local_path
group_name
log_type
machine_id
target_date_yymmdd
drive_size
drive_mtime
content_hash
status              # pending, downloaded, failed, conflict
attempt_count
downloaded_at
last_attempt_at
last_error
```

Status values:

- `pending`: selected but not completed yet
- `uploaded`: uploaded or verified on Drive
- `downloaded`: downloaded or verified locally
- `failed`: retryable error
- `conflict`: same target path exists with different content

## Idempotency and Conflict Handling

Idempotency is based on normalized target path plus file fingerprint.

Fingerprint includes:

- file size
- modified time
- content hash

For CSV files, hashing is acceptable because the expected hourly volume is small: at most one summary file per date per log type per field PC.

Conflict rules:

- Do not overwrite an existing Drive file when the target path exists with different content.
- Do not overwrite an existing local download when the target path exists with different content.
- Record conflicts in SQLite and status JSON.
- Leave conflicted files untouched for manual review.

Retry rules:

- Retry `failed` records on the next hourly run.
- Keep `attempt_count` and `last_error`.
- Do not retry `conflict` automatically.

## Status JSON

Each successful program run should upload or update a small status JSON file in Drive.

Uploader example:

```json
{
  "pc_id": "field-pc-01",
  "role": "uploader",
  "group_name": "Array_MIC",
  "machine_id": "성능검사기_1",
  "last_run_at": "2026-06-18T10:00:00+09:00",
  "last_success_at": "2026-06-18T10:00:03+09:00",
  "processed_count": 4,
  "success_count": 4,
  "failed_count": 0,
  "conflict_count": 0,
  "last_error": null
}
```

Downloader example:

```json
{
  "pc_id": "management-pc-01",
  "role": "downloader",
  "last_run_at": "2026-06-18T10:00:00+09:00",
  "last_success_at": "2026-06-18T10:00:08+09:00",
  "processed_count": 12,
  "success_count": 12,
  "failed_count": 0,
  "conflict_count": 0,
  "last_error": null
}
```

n8n can monitor these files for stale heartbeat, failed records, or conflicts.

## Google Drive Authentication

Use Google Drive API directly rather than Google Drive Desktop sync.

The first implementation will support an installed-app OAuth setup flow:

- `auth` command opens or instructs the user to open Google's OAuth consent flow.
- Each PC stores its own token file under `state_dir`.
- The same Google account or shared Drive folder is used by all PCs.
- The configured Drive root folder ID determines where files are created and read.

This keeps upload and download completion observable by the program.

## Logging

Each PC writes local logs under `state_dir/logs/app.log`.

Logs should include:

- run start and end
- config role and PC ID
- selected file count
- uploaded/downloaded/skipped/failed/conflict counts
- per-file errors
- Google Drive authentication errors

Logs should not include OAuth secrets or token contents.

## Testing Strategy

Unit tests:

- source folder to log type mapping
- `YYYYMMDD` to `YYMMDD` conversion
- Drive path generation
- selection of only `总数据` CSV files
- ignoring `fail` and unknown folders
- SQLite idempotency checks
- conflict classification

Integration-style tests with a fake Drive adapter:

- upload creates expected virtual Drive path
- repeated upload skips duplicates
- existing same-content Drive file is treated as success
- existing different-content Drive file is treated as conflict
- download creates expected local path
- repeated download skips duplicates

Manual verification:

- run uploader against a sample `E:\`-like fixture directory
- confirm only four daily summary CSVs upload for a date when all log types exist
- run downloader into a temp folder
- confirm downloaded structure matches `Array_MIC/{log_type}/{machine_id}/{YYMMDD}/{YYMMDD}_{log_type}.csv`

## Implementation Boundaries

The implementation keeps these modules separate:

- config loading and validation
- local source scanning
- normalized path mapping
- Google Drive adapter
- SQLite state repository
- uploader workflow
- downloader workflow
- status JSON writer
- CLI entry point

This separation keeps the Google Drive API replaceable and allows most behavior to be tested without network access.
