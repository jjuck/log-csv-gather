from pathlib import Path

from log_csv_gather.config import AppConfig
from log_csv_gather.drive import build_drive_service


def test_build_drive_service_uses_service_account_file_without_token(tmp_path: Path, monkeypatch) -> None:
    service_account_file = tmp_path / "service-account.json"
    service_account_file.write_text("{}", encoding="utf-8")
    token_file = tmp_path / "state" / "token.json"
    config = AppConfig(
        role="downloader",
        pc_id="management-pc-01",
        drive_root_folder_id="drive-root-id",
        state_dir=tmp_path / "state",
        download_root=tmp_path / "downloads",
        service_account_file=service_account_file,
        token_file=token_file,
    )
    calls = {}

    def fake_from_service_account_file(path, scopes):
        calls["service_account_file"] = path
        calls["scopes"] = scopes
        return "service-account-credentials"

    class FakeHttp:
        def __init__(self, timeout):
            calls["timeout"] = timeout

    class FakeAuthorizedHttp:
        def __init__(self, credentials, http):
            self.credentials = credentials
            self.http = http
            calls["authorized_http"] = self

    def fake_build(api_name, api_version, http, cache_discovery):
        calls["build"] = (api_name, api_version, http, cache_discovery)
        return "drive-service"

    import google_auth_httplib2
    import httplib2
    import google.oauth2.service_account as service_account_module
    import googleapiclient.discovery as discovery_module

    monkeypatch.setattr(httplib2, "Http", FakeHttp)
    monkeypatch.setattr(google_auth_httplib2, "AuthorizedHttp", FakeAuthorizedHttp)
    monkeypatch.setattr(
        service_account_module.Credentials,
        "from_service_account_file",
        staticmethod(fake_from_service_account_file),
    )
    monkeypatch.setattr(discovery_module, "build", fake_build)

    service = build_drive_service(config)

    assert service == "drive-service"
    assert calls["service_account_file"] == str(service_account_file)
    assert calls["scopes"] == ["https://www.googleapis.com/auth/drive"]
    assert calls["timeout"] == 60
    assert calls["authorized_http"].credentials == "service-account-credentials"
    assert isinstance(calls["authorized_http"].http, FakeHttp)
    assert calls["build"] == ("drive", "v3", calls["authorized_http"], False)
    assert not token_file.exists()
