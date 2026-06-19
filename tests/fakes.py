from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from log_csv_gather.drive import DriveFile


@dataclass
class StoredFile:
    file: DriveFile
    content: bytes


class FakeDriveAdapter:
    def __init__(self) -> None:
        self.files_by_path: dict[str, StoredFile] = {}
        self.json_by_path: dict[str, dict[str, Any]] = {}
        self._next_id = 1

    def put_file(self, path: str, content: bytes, modified_time: str = "2026-06-18T10:00:00Z") -> DriveFile:
        drive_file = DriveFile(
            id=f"drive-{self._next_id}",
            path=path,
            name=Path(path).name,
            size=len(content),
            md5_checksum=hashlib.md5(content).hexdigest(),
            modified_time=modified_time,
        )
        self._next_id += 1
        self.files_by_path[path] = StoredFile(drive_file, content)
        return drive_file

    def find_file(self, drive_path: str) -> DriveFile | None:
        stored = self.files_by_path.get(drive_path)
        return stored.file if stored else None

    def upload_file(self, local_path: Path, drive_path: str) -> DriveFile:
        return self.put_file(drive_path, local_path.read_bytes())

    def list_files(self, prefix: str = "logs/") -> list[DriveFile]:
        return [
            stored.file
            for path, stored in sorted(self.files_by_path.items())
            if path.startswith(prefix)
        ]

    def download_file(self, drive_file_id: str, local_path: Path) -> DriveFile:
        for stored in self.files_by_path.values():
            if stored.file.id == drive_file_id:
                local_path.parent.mkdir(parents=True, exist_ok=True)
                local_path.write_bytes(stored.content)
                return stored.file
        raise FileNotFoundError(drive_file_id)

    def upsert_json(self, drive_path: str, data: dict[str, Any]) -> DriveFile:
        self.json_by_path[drive_path] = data
        content = repr(data).encode("utf-8")
        return self.put_file(drive_path, content)
