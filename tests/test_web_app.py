from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from log_csv_gather.config import AppConfig
def _config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        role="uploader",
        pc_id="field-pc-01",
        drive_root_folder_id="drive-root-id",
        state_dir=tmp_path / "state",
        source_root=tmp_path / "source",
        group_name="Array_MIC",
        machine_id="machine-1",
    )


def test_health_endpoint_reports_local_runtime_context(tmp_path: Path) -> None:
    from log_csv_gather.web.app import create_app

    config = _config(tmp_path)
    app = create_app(config, config_path=tmp_path / "config.yaml", port=8765)
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "ok": True,
        "app": "log-csv-gather",
        "role": "uploader",
        "pc_id": "field-pc-01",
        "host": "127.0.0.1",
        "port": 8765,
        "url": "http://127.0.0.1:8765",
        "config_path": str(tmp_path / "config.yaml"),
        "state_dir": str(tmp_path / "state"),
    }


def test_dashboard_shell_uses_local_assets_and_operations_layout(tmp_path: Path) -> None:
    from log_csv_gather.web.app import create_app

    config = _config(tmp_path)
    app = create_app(config, config_path=tmp_path / "config.yaml", port=8765)
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    html = response.text
    assert "CSV Ops Console" in html
    assert "data-action=\"upload-dry-run\"" in html
    assert "data-action-status=\"upload-dry-run\"" in html
    assert "data-job-progress-bar" in html
    assert "data-job-progress-text" in html
    assert "data-job-counts" in html
    assert "data-feed" in html
    assert "data-log-tail" in html
    assert "System Status" in html
    assert "Scheduler" in html
    assert "data-scheduler-register" in html
    assert "data-scheduler-interval" in html
    assert "data-role-switch=\"uploader\"" in html
    assert "data-role-switch=\"downloader\"" in html
    assert "data-active-reset" in html
    assert "data-setup-open" in html
    assert "data-setup-modal" in html
    assert "data-folder-browser" in html
    assert "Current Job Feed" in html
    assert "Config Summary" in html
    assert "app.log" in html
    assert "https://" not in html
    assert "cdn." not in html
    assert "/static/app.css" in html
    assert "/static/app.js" in html


def test_static_assets_are_served_with_no_store_cache_policy(tmp_path: Path) -> None:
    from log_csv_gather.web.app import create_app

    config = _config(tmp_path)
    app = create_app(config, config_path=tmp_path / "config.yaml", port=8765)
    client = TestClient(app)

    response = client.get("/static/app.css")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "--surface:" in response.text
