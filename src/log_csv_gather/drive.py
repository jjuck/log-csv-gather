from __future__ import annotations

import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class DriveFile:
    id: str
    path: str
    name: str
    size: int
    md5_checksum: str
    modified_time: str


class DriveAdapter(Protocol):
    def find_file(self, drive_path: str) -> DriveFile | None:
        ...

    def upload_file(self, local_path: Path, drive_path: str) -> DriveFile:
        ...

    def list_files(self, prefix: str = "logs/") -> list[DriveFile]:
        ...

    def download_file(self, drive_file_id: str, local_path: Path) -> DriveFile:
        ...

    def upsert_json(self, drive_path: str, data: dict[str, Any]) -> DriveFile:
        ...


class GoogleDriveAdapter:
    FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"

    def __init__(self, service: Any, root_folder_id: str, num_retries: int = 3) -> None:
        self.service = service
        self.root_folder_id = root_folder_id
        self.num_retries = num_retries
        self._folder_cache: dict[tuple[str, str], str] = {}

    @classmethod
    def from_config(cls, config: Any) -> "GoogleDriveAdapter":
        service = build_drive_service(config)
        return cls(service, config.drive_root_folder_id, num_retries=config.drive_num_retries)

    def find_file(self, drive_path: str) -> DriveFile | None:
        parent_id, name = self._resolve_parent(drive_path, create=False)
        if parent_id is None:
            return None
        item = self._find_child(parent_id, name, folder=False)
        if item is None:
            return None
        return self._to_drive_file(item, drive_path)

    def upload_file(self, local_path: Path, drive_path: str) -> DriveFile:
        from googleapiclient.http import MediaFileUpload

        parent_id, name = self._resolve_parent(drive_path, create=True)
        media = MediaFileUpload(str(local_path), mimetype="text/csv", resumable=True)
        body = {"name": name, "parents": [parent_id]}
        item = (
            self.service.files()
            .create(body=body, media_body=media, fields="id,name,size,md5Checksum,modifiedTime")
        )
        item = self._execute(item)
        return self._to_drive_file(item, drive_path)

    def list_files(self, prefix: str = "logs/") -> list[DriveFile]:
        clean_prefix = prefix.strip("/")
        if not clean_prefix:
            start_id = self.root_folder_id
            start_path = ""
        else:
            start_id = self._resolve_folder_path(clean_prefix, create=False)
            if start_id is None:
                return []
            start_path = clean_prefix
        return self._list_files_recursive(start_id, start_path)

    def download_file(self, drive_file_id: str, local_path: Path) -> DriveFile:
        from googleapiclient.http import MediaIoBaseDownload

        metadata = (
            self.service.files()
            .get(fileId=drive_file_id, fields="id,name,size,md5Checksum,modifiedTime")
        )
        metadata = self._execute(metadata)
        request = self.service.files().get_media(fileId=drive_file_id)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        with local_path.open("wb") as file:
            downloader = MediaIoBaseDownload(file, request)
            done = False
            while not done:
                _, done = downloader.next_chunk(num_retries=self.num_retries)
        return DriveFile(
            id=metadata["id"],
            path="",
            name=metadata["name"],
            size=int(metadata.get("size", 0)),
            md5_checksum=metadata.get("md5Checksum", ""),
            modified_time=metadata.get("modifiedTime", ""),
        )

    def upsert_json(self, drive_path: str, data: dict[str, Any]) -> DriveFile:
        from googleapiclient.http import MediaIoBaseUpload

        parent_id, name = self._resolve_parent(drive_path, create=True)
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        media = MediaIoBaseUpload(io.BytesIO(payload), mimetype="application/json", resumable=True)
        existing = self._find_child(parent_id, name, folder=False)
        body = {"name": name}
        if existing:
            item = (
                self.service.files()
                .update(fileId=existing["id"], body=body, media_body=media, fields="id,name,size,md5Checksum,modifiedTime")
            )
            item = self._execute(item)
        else:
            body["parents"] = [parent_id]
            item = (
                self.service.files()
                .create(body=body, media_body=media, fields="id,name,size,md5Checksum,modifiedTime")
            )
            item = self._execute(item)
        return self._to_drive_file(item, drive_path)

    def _resolve_parent(self, drive_path: str, create: bool) -> tuple[str | None, str]:
        parts = [part for part in drive_path.split("/") if part]
        if not parts:
            raise ValueError("drive_path must not be empty")
        parent_parts = parts[:-1]
        parent_id = self._resolve_folder_path("/".join(parent_parts), create=create) if parent_parts else self.root_folder_id
        return parent_id, parts[-1]

    def _resolve_folder_path(self, folder_path: str, create: bool) -> str | None:
        current = self.root_folder_id
        for part in [item for item in folder_path.split("/") if item]:
            cached = self._folder_cache.get((current, part))
            if cached:
                current = cached
                continue
            parent_id = current
            child = self._find_child(current, part, folder=True)
            if child is None:
                if not create:
                    return None
                body = {"name": part, "mimeType": self.FOLDER_MIME_TYPE, "parents": [current]}
                child = self._execute(self.service.files().create(body=body, fields="id,name"))
            current = child["id"]
            self._folder_cache[(parent_id, part)] = current
        return current

    def _find_child(self, parent_id: str, name: str, folder: bool) -> dict[str, Any] | None:
        escaped = name.replace("'", "\\'")
        query = f"name = '{escaped}' and '{parent_id}' in parents and trashed = false"
        if folder:
            query += f" and mimeType = '{self.FOLDER_MIME_TYPE}'"
        else:
            query += f" and mimeType != '{self.FOLDER_MIME_TYPE}'"
        response = (
            self.service.files()
            .list(q=query, spaces="drive", fields="files(id,name,size,md5Checksum,modifiedTime,mimeType)", pageSize=10)
        )
        response = self._execute(response)
        files = response.get("files", [])
        return files[0] if files else None

    def _list_files_recursive(self, folder_id: str, path_prefix: str) -> list[DriveFile]:
        files: list[DriveFile] = []
        page_token = None
        while True:
            response = (
                self.service.files()
                .list(
                    q=f"'{folder_id}' in parents and trashed = false",
                    spaces="drive",
                    fields="nextPageToken,files(id,name,size,md5Checksum,modifiedTime,mimeType)",
                    pageToken=page_token,
                    pageSize=1000,
                )
            )
            response = self._execute(response)
            for item in response.get("files", []):
                child_path = "/".join(part for part in [path_prefix, item["name"]] if part)
                if item.get("mimeType") == self.FOLDER_MIME_TYPE:
                    files.extend(self._list_files_recursive(item["id"], child_path))
                else:
                    files.append(self._to_drive_file(item, child_path))
            page_token = response.get("nextPageToken")
            if not page_token:
                return files

    @staticmethod
    def _to_drive_file(item: dict[str, Any], drive_path: str) -> DriveFile:
        return DriveFile(
            id=item["id"],
            path=drive_path,
            name=item["name"],
            size=int(item.get("size", 0)),
            md5_checksum=item.get("md5Checksum", ""),
            modified_time=item.get("modifiedTime", ""),
        )

    def _execute(self, request: Any) -> Any:
        return request.execute(num_retries=self.num_retries)


def build_drive_service(config: Any) -> Any:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google.oauth2 import service_account
    from google_auth_oauthlib.flow import InstalledAppFlow
    import google_auth_httplib2
    import httplib2
    from googleapiclient.discovery import build

    scopes = ["https://www.googleapis.com/auth/drive"]
    timeout_seconds = getattr(config, "drive_timeout_seconds", 60)
    if getattr(config, "service_account_file", None):
        credentials = service_account.Credentials.from_service_account_file(
            str(config.service_account_file),
            scopes=scopes,
        )
        http = httplib2.Http(timeout=timeout_seconds)
        authorized_http = google_auth_httplib2.AuthorizedHttp(credentials, http=http)
        return build("drive", "v3", http=authorized_http, cache_discovery=False)

    token_file = config.token_file or (config.state_dir / "token.json")
    credentials_file = config.credentials_file or (config.state_dir / "credentials.json")
    credentials = None
    if token_file.exists():
        credentials = Credentials.from_authorized_user_file(str(token_file), scopes)
    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            if not credentials_file.exists():
                raise FileNotFoundError(f"Google OAuth credentials file not found: {credentials_file}")
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), scopes)
            credentials = flow.run_local_server(port=0)
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(credentials.to_json(), encoding="utf-8")
    http = httplib2.Http(timeout=timeout_seconds)
    authorized_http = google_auth_httplib2.AuthorizedHttp(credentials, http=http)
    return build("drive", "v3", http=authorized_http, cache_discovery=False)
