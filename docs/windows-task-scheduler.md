# Windows Task Scheduler

The project is designed to run about once per hour from Windows Task Scheduler, but operators should normally manage the task from the local dashboard.

`C:\log-csv-gather` in examples is only a placeholder. The project can live in any stable directory. Relative paths inside YAML are resolved from the config file directory.

## Operator Flow

Normal instruction:

```text
Double-click run.bat
```

`run.bat` starts the FastAPI dashboard on `127.0.0.1`. The dashboard prefers port `8765`, falls back to the next free port, reuses an existing healthy server, and throttles browser auto-open events so double-clicking does not open repeated tabs.

On first launch, `run.bat` creates or uses `configs\active.yaml`. If both production templates are present and active config is missing, it asks for the PC role once and saves the selected template as `active.yaml`.

After the dashboard opens:

1. Open Setup and save the PC-specific values.
2. Click Auth if OAuth has not been completed on this PC.
3. Click Doctor manually.
4. Run Dry-run.
5. Run Once.
6. Register the scheduler from the Scheduler panel only after checks pass.

Saving setup never registers the scheduler automatically. If setup changes are saved or Settings Reset is used while a scheduled task is already registered, the dashboard unregisters the old task and shows that registration is needed again.

## Recommended Auth Mode

Use OAuth for personal Google Drive or a shared folder owned by a normal Google account.

1. Create a Google Cloud OAuth Desktop client.
2. Save the downloaded JSON as:

```powershell
<project-dir>\secrets\oauth-client.json
```

3. Set each template or active config:

```yaml
credentials_file: "../secrets/oauth-client.json"
token_file: "../runtime/production/uploader-state/token.json"
```

Use a different `token_file` per PC role or per Windows user.

Service account UX is not part of the current operator flow.

## Dashboard Scheduler Control

The dashboard can query, register/update, enable, disable, and unregister the hourly task. The UI shows the repeat interval in hours; the YAML stores the equivalent minutes for compatibility.

The scheduler panel persists these values back to `configs\active.yaml`:

```yaml
scheduler:
  enabled: true
  interval_minutes: 60
  task_name:
```

When `run.bat` is present, the registered task calls:

```powershell
<project-dir>\run.bat upload-once <project-dir>\configs\active.yaml
```

or, for a downloader:

```powershell
<project-dir>\run.bat download-once <project-dir>\configs\active.yaml
```

The task runs under the current Windows user. If the PC policy blocks task creation, use a Windows account allowed to create scheduled tasks or register the task manually.

## Manual Uploader Command

For source-checkout development or manual diagnosis:

```powershell
python -m log_csv_gather upload --config <project-dir>\configs\active.yaml --dry-run
python -m log_csv_gather upload --config <project-dir>\configs\active.yaml
```

For portable operation:

```powershell
<project-dir>\run.bat upload-once <project-dir>\configs\active.yaml
```

## Manual Downloader Command

For source-checkout development or manual diagnosis:

```powershell
python -m log_csv_gather download --config <project-dir>\configs\active.yaml --dry-run
python -m log_csv_gather download --config <project-dir>\configs\active.yaml
```

For portable operation:

```powershell
<project-dir>\run.bat download-once <project-dir>\configs\active.yaml
```

## Drive Stability Options

These values are recommended for field PCs where the network can be unstable:

```yaml
drive_timeout_seconds: 60
drive_num_retries: 3
```

`drive_timeout_seconds` controls the per-request HTTP timeout. `drive_num_retries` is passed to Google Drive API request execution.

Workflow hardening should treat network failures as retryable, continue to the next file, and retry failed items once near the end of the job. Structural Auth, Drive root, permission, quota, or local root errors should stop early.

## Progress And Logs

Console progress is controlled by:

```yaml
progress_every: 10
```

The dashboard Current Job panel will show structured progress. Feed should show major events only.

Logs are written under:

```powershell
{state_dir}\logs\app.log
```

The launcher sets UTF-8 related environment variables so Korean paths and machine names are preserved in the web log tail.

Drive status files:

```text
status/uploaders/{pc_id}.json
status/downloaders/{pc_id}.json
status/doctor/{pc_id}.json
```

If the status JSON update fails, upload/download results are still preserved locally in SQLite and the warning is written to the app log.
