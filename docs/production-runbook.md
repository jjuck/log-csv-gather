# Production Runbook

This runbook is the practical checklist for moving from a sample test folder to real field and management PCs.

## 1. Directory Layout

The project directory does not need to be `C:\log-csv-gather`. Pick one stable location per PC.

Suggested layout:

```text
<project-dir>\
  configs\
    active.yaml
    production.uploader.yaml
    production.downloader.yaml
  secrets\
    oauth-client.json
  runtime\
    production\
      uploader-state\
      downloader-state\
  run.bat
  requirements.txt
```

`configs`, `secrets`, and `runtime` are intentionally git-ignored because they contain local machine settings, credentials, tokens, logs, or SQLite state.

Relative paths in YAML are resolved from the config file directory. If the config is `configs/active.yaml`, then `../secrets/oauth-client.json` points to `<project-dir>\secrets\oauth-client.json`.

## 2. Operator Entry Point

For normal use, double-click:

```text
run.bat
```

On first launch, if `configs\active.yaml` does not exist and both production templates are present, the launcher asks for the PC role once:

```text
1. Field PC upload dashboard
2. Management PC download dashboard
```

The selected template is copied to `configs\active.yaml`. Future launches use `active.yaml` automatically.

The dashboard Setup button is the preferred way to finish or correct PC-specific settings. Setup edits only `configs\active.yaml`; production configs remain templates.

Setup fields:

- role: field PC uploader or management PC downloader
- PC name: local program identity used for status files
- Drive root folder ID
- uploader machine name, default `성능검사기_1`
- uploader log root folder, usually `E:\`
- downloader download root folder

The setup dialog includes a local folder browser. It shows drives and folders from the current PC, hides files, and validates the selected folder immediately. For uploaders, validation checks whether the four expected source folders are present. For downloaders, validation checks whether the destination can be created and written.

Saving setup does not run Doctor and does not register the scheduler. After saving, run Doctor manually from the dashboard and review the result before registering the scheduled task.

If setup changes are saved while a scheduled task is already registered, the dashboard unregisters the old task and shows that Scheduler registration is needed again.

## 3. Uploader Config

Production uploader templates should contain stable hidden defaults. Operators normally edit only through the dashboard.

Required values:

```yaml
role: uploader
pc_id: field-pc-01
group_name: Array_MIC
machine_id: 성능검사기_1
drive_root_folder_id: replace-with-google-drive-folder-id
source_root: "E:\\"
state_dir: "../runtime/production/uploader-state"
credentials_file: "../secrets/oauth-client.json"
token_file: "../runtime/production/uploader-state/token.json"
file_stable_minutes: 5
progress_every: 10
drive_timeout_seconds: 60
drive_num_retries: 3
web:
  host: "127.0.0.1"
  preferred_port: 8765
  open_browser: true
  browser_open_throttle_seconds: 3
scheduler:
  enabled: true
  interval_minutes: 60
  task_name:
```

Only these source folders are scanned:

```yaml
log_type_mappings:
  "PAS Test data": "PAS"
  "HM-3203-011 Test data": "3203"
  "HM-3903-011 Test data": "3903"
  "LITE Test data": "LITE"
ignore_dirs:
  - fail
```

The uploader processes only date folders named exactly `YYYYMMDD` and CSV files whose name contains `总数据`.

## 4. Downloader Config

Required values:

```yaml
role: downloader
pc_id: management-pc-01
drive_root_folder_id: replace-with-google-drive-folder-id
download_root: "../runtime/downloads"
state_dir: "../runtime/production/downloader-state"
credentials_file: "../secrets/oauth-client.json"
token_file: "../runtime/production/downloader-state/token.json"
progress_every: 10
drive_timeout_seconds: 60
drive_num_retries: 3
web:
  host: "127.0.0.1"
  preferred_port: 8765
  open_browser: true
  browser_open_throttle_seconds: 3
scheduler:
  enabled: true
  interval_minutes: 60
  task_name:
include_groups:
  - Array_MIC
include_log_types:
  - PAS
  - "3203"
  - "3903"
  - LITE
```

## 5. OAuth Setup

Use OAuth for the current production path. Service account UX is deferred.

1. Create a Google Cloud OAuth Desktop client.
2. Save the downloaded JSON as `secrets\oauth-client.json`.
3. Start the dashboard with `run.bat`.
4. Click Auth once on each PC while the Windows user is logged in.
5. Click Doctor manually after Auth and after setup changes.

Doctor should report:

```text
doctor: config=ok auth=ok drive_root=ok write_status=ok
```

## 6. Scheduler Registration

Scheduler registration is always a separate operator action.

Before registering the hourly task:

- Setup has been saved in the dashboard.
- Auth has succeeded.
- Doctor has succeeded.
- Dry-run shows the expected candidate count.
- A manual Once run succeeds or has only expected retryable failures.

Then use the dashboard Scheduler panel:

1. Set the interval in minutes. The default is `60`.
2. Click register/update.
3. Confirm the task is registered and enabled.

The dashboard persists scheduler settings back to `configs\active.yaml`.

## 7. Go/No-Go Checks

Before leaving the PC unattended:

- `/api/health` is reachable from the local dashboard.
- `status` shows upload or download counts.
- `{state_dir}\logs\app.log` has no repeated structural failures.
- Google Drive has `status/uploaders/{pc_id}.json` or `status/downloaders/{pc_id}.json`.
- The dashboard Scheduler panel can register, disable, enable, and unregister the task without errors.

## 8. Retry And Conflict Rules

Retryable network failures such as timeouts should not stop the whole batch. The workflow continues to the next file, then retries failed items once near the end of the job after a short wait. Any remaining `failed` items are retried by the next scheduler run or manual Once.

Structural errors stop early because retries are unlikely to help:

- OAuth token problems
- Drive root folder ID missing or inaccessible
- insufficient Drive permissions
- Google Drive API/project errors
- Drive storage quota errors
- uploader source root inaccessible
- downloader destination root cannot be created or written

`conflict` means different content already exists at the normalized target path. Conflict items are not retried automatically.

Operational conflict rule:

- Leave original files untouched.
- Check `status --details` and `{state_dir}\logs\app.log`.
- Compare the local source or destination file with the Drive file manually.
- Decide which file should be retained before deleting or renaming anything.
