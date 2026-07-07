# Local Web Dashboard

The local dashboard is the lightweight UI layer for field and management PCs.

## Operator UX Decisions

The dashboard is local-only and is designed so a non-programmer can operate it by double-clicking `run.bat`.

Current agreed UX rules:

- `configs/active.yaml` is the only mutable per-PC config used by the dashboard.
- `configs/production.uploader.yaml` and `configs/production.downloader.yaml` are templates/recovery sources.
- The initial setup dialog edits `active.yaml`; it does not edit the production templates.
- The setup dialog exposes only the operational fields operators need: role, PC name, Drive root folder ID, uploader log root, downloader download root, and uploader machine name.
- OAuth client secret and token paths stay hidden behind the configured defaults.
- Service account UX is deferred. The dashboard flow assumes OAuth.
- Saving setup does not run Doctor automatically.
- Saving setup does not register the Windows scheduled task automatically.
- After setup changes, the UI should mark Doctor as verification-needed and prompt the operator to run Doctor manually.
- If setup changes are saved while a scheduled task is registered, the dashboard unregisters the old task and shows that scheduler registration is needed again.
- If the operator runs Once or registers the scheduler after setup changes but before Doctor, the UI warns and asks for confirmation instead of hard-blocking.

## Setup Dialog And Folder Browser

Phase 1 of the next production hardening work adds an initial setup dialog:

- A visible Setup button opens the dialog at any time.
- The dialog opens automatically when `active.yaml` is missing or the current config still looks incomplete, such as a placeholder Drive folder ID.
- Role-specific fields are shown:
  - uploader: PC name, Drive root folder ID, machine name, and log root folder.
  - downloader: PC name, Drive root folder ID, and download root folder.
- PC name and machine name allow Korean text. Spaces are normalized to `_`; path-unsafe characters are rejected.
- The default machine name is `성능검사기_1`.
- The uploader log root is the parent folder containing the mapped source folders.
- The downloader root is the local destination folder.

The dashboard provides a local folder browser through FastAPI because the browser and Python server run on the same PC:

```text
GET  /api/local/drives
GET  /api/local/folders?path=<path>
POST /api/local/validate-path
POST /api/config/setup
```

Folder browser rules:

- Show local and mapped drives such as `C:\`, `D:\`, and `E:\`.
- Show folders only; files are hidden.
- Hide hidden/system folders by default.
- Never crash on access-denied folders; return a readable warning.
- Selecting a folder immediately runs path validation.
- Validation warnings do not block saving.

Uploader validation checks the five source folders:

```text
PAS Test data
HM-3203-011 Test data
HM-3903-011 Test data
LITE Test data
SMIC_Test data
```

Downloader validation checks whether the destination exists or can be created and written.

## Phase 1 Scope

Phase 1 provides the runtime and UI foundation:

- `python -m log_csv_gather web --config <config.yaml>`
- `run.bat web <config.yaml>`
- FastAPI local server
- `127.0.0.1` only
- preferred port `8765`
- sequential port fallback when the preferred port is occupied
- `state_dir/web/server.json`
- `/api/health`
- browser auto-open with `state_dir/web/browser-opened.json` throttle
- local-only HTML/CSS/JS dashboard shell

Phase 2 wires the dashboard controls to the local job API.

## Phase 2 Scope

Phase 2 provides:

- REST action endpoints
- background job queue
- duplicate action protection
- 1-2 second polling feed
- status/details API
- app log tail API
- role-aware action buttons

```text
POST /api/actions/auth
POST /api/actions/doctor
POST /api/actions/upload-dry-run
POST /api/actions/upload-once
POST /api/actions/download-dry-run
POST /api/actions/download-once
GET  /api/jobs/{job_id}
GET  /api/feed
GET  /api/status?details=true
GET  /api/logs/tail?lines=120
```

Uploader configs can run upload actions. Downloader configs can run download actions. Common actions are `auth` and `doctor`.

## Phase 3 Scope

Phase 3 lets the local dashboard manage the hourly Windows Task Scheduler loop:

- read the configured task name, interval, enabled flag, and current registration state
- register or update the task from the dashboard
- change the repeat interval in hours
- enable or disable the registered task
- unregister the task
- persist scheduler changes back to the same YAML config
- switch this PC between uploader and downloader active configs
- unregister the current scheduled task and reset `configs/active.yaml` from the visible Settings Reset button
- reset the local SQLite processing state from the visible Local State Reset button

```text
GET  /api/scheduler
POST /api/scheduler/register
POST /api/scheduler/enable
POST /api/scheduler/disable
POST /api/scheduler/unregister
GET  /api/config/active
POST /api/config/role
POST /api/config/active/reset
POST /api/state/reset
```

`POST /api/state/reset` backs up `{state_dir}/state.sqlite` and clears only local processing history, action button state, conflict, and failed records. It preserves `active.yaml`, OAuth token files, original CSV files, and Google Drive files.

`POST /api/config/active/reset` unregisters the current scheduled task when one exists, then deletes `configs/active.yaml`.

`POST /api/scheduler/register` accepts:

```json
{
  "interval_minutes": 60,
  "enabled": true
}
```

The dashboard shows the interval in hours and sends the equivalent `interval_minutes` value to the API. It writes `scheduler.enabled` and `scheduler.interval_minutes` back to the config file before registering the task. The registered task calls the portable launcher when `run.bat` is present:

```text
run.bat upload-once <config-path>
run.bat download-once <config-path>
```

If `run.bat` is not present, it falls back to the current Python executable and `python -m log_csv_gather upload|download --config <config-path>`.

Role switching copies one of these files to `configs/active.yaml`:

```text
configs/production.uploader.yaml
configs/production.downloader.yaml
```

When a role switch is requested and the current role has a registered scheduled task, the dashboard unregisters the current task before changing `active.yaml`.

The setup dialog supersedes role-only switching for normal operators. Role switching remains as support behavior, while `/api/config/setup` is the preferred path for editing the active config.

## Observability UX

The dashboard keeps the last result for `auth`, `doctor`, dry-run, and once actions in SQLite so button state survives browser refresh and app restart.

Color rules:

- gray: never run
- blue: running
- green: succeeded with no failed/conflict items
- yellow: partial failure with retryable failed items
- amber: conflict exists and needs administrator review
- red: structural failure, Auth failure, or Doctor failure

Progress bars are only for Current Job:

- upload/download/dry-run: current file count over total candidate count
- Doctor/Auth: step-based progress
- failed-item retry: separate phase after the first pass

Feed and progress are separate. Current Job uses the latest structured progress payload; Feed shows major events only and is returned newest-first.

## Failure Handling UX

Retry policy:

- Retryable network failures continue to the next file.
- At the end of the job, failed items wait 10 seconds and retry once.
- Items still failing remain `failed` and retry on the next scheduler run or manual Once.
- `conflict` is never retried automatically.
- Structural Drive/Auth/local-root errors stop the job early.

Doctor checks both Drive and local readiness:

- uploader source root
- uploader mapped source folders
- downloader destination root
- Drive root access
- Drive status JSON write

## Config

The existing YAML remains the single source of truth.

Path values such as `state_dir`, `credentials_file`, `token_file`, `source_root`, and `download_root` may be absolute or relative. Relative paths are resolved from the YAML config file directory.

```yaml
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

Only `127.0.0.1` is supported for `web.host` in the local dashboard.

## Runtime Files

```text
{state_dir}/web/server.json
{state_dir}/web/browser-opened.json
```

`server.json` records the active server process metadata. When a new launcher starts, it calls `/api/health` on the recorded URL. If the server is healthy and matches the same `pc_id` and `role`, the launcher reuses that dashboard instead of starting another one.

`browser-opened.json` records the last browser open event. It prevents duplicate browser windows when `run.bat` is double-clicked repeatedly.

## WebSocket Plan

WebSocket is not part of v1. The dashboard uses REST actions and 1-2 second polling:

```text
POST /api/actions/{action}
GET /api/jobs/{job_id}
GET /api/feed
```

Future WebSocket support can be added as a feed transport:

```text
GET /ws/feed
```

The durable state should remain REST/job based so the UI can recover after refreshes, closed tabs, browser sleep, or reconnects.
