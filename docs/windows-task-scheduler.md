# Windows Task Scheduler

This project is designed to run once per hour from Windows Task Scheduler.

## Recommended Auth Mode

Use OAuth for personal Google Drive or a shared folder owned by a normal Google account.

1. Create a Google Cloud OAuth Desktop client.
2. Save the downloaded JSON as:

```powershell
C:\log-csv-gather\secrets\oauth-client.json
```

3. Set each config file:

```yaml
credentials_file: "C:\\log-csv-gather\\secrets\\oauth-client.json"
token_file: "C:\\log-csv-gather\\state\\uploader\\token.json"
```

Use a different `token_file` per PC role or per Windows user.

Service accounts are not the default path for this project. They do not use a personal Google Drive account's 15 GB storage quota. Keep service account mode only for a Google Workspace Shared Drive setup.

## One-Time Setup

Run auth once on each PC while the Windows user is logged in:

```powershell
python -m log_csv_gather auth --config C:\log-csv-gather\uploader.config.yaml
```

After auth succeeds, run the doctor check:

```powershell
python -m log_csv_gather doctor --config C:\log-csv-gather\uploader.config.yaml
```

Expected output:

```text
doctor: config=ok auth=ok drive_root=ok write_status=ok
```

If `doctor` fails, fix that before registering the hourly task.

## Uploader Task

Run this on each field PC:

```powershell
python -m log_csv_gather upload --config C:\log-csv-gather\uploader.config.yaml
```

Suggested trigger:

- Daily
- Repeat task every 1 hour
- Run whether user is logged on or not
- Start in the project or installed package directory

## Downloader Task

Run this on each management PC:

```powershell
python -m log_csv_gather download --config C:\log-csv-gather\downloader.config.yaml
```

Users can run the same command manually when they want an immediate sync.

## Progress And Logs

Set `progress_every` in the config to control console progress output:

```yaml
progress_every: 10
```

Logs are written under:

```powershell
{state_dir}\logs\app.log
```

The app also updates Drive status files:

```text
status/uploaders/{pc_id}.json
status/downloaders/{pc_id}.json
status/doctor/{pc_id}.json
```

If the status JSON update fails, upload/download results are still preserved locally in SQLite and the warning is written to the app log.
