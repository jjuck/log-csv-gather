from __future__ import annotations

import os
import string
from pathlib import Path
from typing import Any

from log_csv_gather.config import DEFAULT_LOG_TYPE_MAPPINGS

WINDOWS_HIDDEN = 0x2
WINDOWS_SYSTEM = 0x4


def list_drives() -> list[dict[str, str]]:
    if os.name == "nt":
        drives: list[dict[str, str]] = []
        for letter in string.ascii_uppercase:
            path = Path(f"{letter}:\\")
            if path.exists():
                drives.append({"name": f"{letter}:\\", "path": str(path)})
        return drives
    return [{"name": "/", "path": "/"}]


def list_folders(path: str | Path) -> dict[str, Any]:
    root = Path(path).expanduser().resolve(strict=False)
    if not root.exists():
        return {
            "path": str(root),
            "parent": str(root.parent) if root.parent != root else None,
            "folders": [],
            "error": f"path does not exist: {root}",
        }
    if not root.is_dir():
        return {
            "path": str(root),
            "parent": str(root.parent) if root.parent != root else None,
            "folders": [],
            "error": f"path is not a directory: {root}",
        }

    folders: list[dict[str, str]] = []
    error = None
    try:
        children = sorted(root.iterdir(), key=lambda item: item.name.lower())
    except OSError as exc:
        children = []
        error = str(exc)

    for child in children:
        try:
            if not child.is_dir() or _is_hidden_or_system(child):
                continue
        except OSError:
            continue
        folders.append({"name": child.name, "path": str(child)})

    return {
        "path": str(root),
        "parent": str(root.parent) if root.parent != root else None,
        "folders": folders,
        "error": error,
    }


def validate_local_path(role: str, path: str | Path) -> dict[str, Any]:
    root = Path(path).expanduser().resolve(strict=False)
    if role == "uploader":
        return _validate_uploader_root(root)
    if role == "downloader":
        return _validate_downloader_root(root)
    return {"role": role, "path": str(root), "status": "error", "message": "unknown role"}


def _validate_uploader_root(root: Path) -> dict[str, Any]:
    expected = list(DEFAULT_LOG_TYPE_MAPPINGS.keys())
    if not root.exists() or not root.is_dir():
        return {
            "role": "uploader",
            "path": str(root),
            "status": "error",
            "message": "log root folder was not found",
            "expected": expected,
            "found": [],
            "missing": expected,
            "found_count": 0,
        }

    found = [name for name in expected if (root / name).is_dir()]
    missing = [name for name in expected if name not in found]
    if len(found) == len(expected):
        status = "ok"
        message = "all expected source folders were found"
    elif found:
        status = "warning"
        message = "only some expected source folders were found"
    else:
        status = "error"
        message = "no expected source folders were found"
    return {
        "role": "uploader",
        "path": str(root),
        "status": status,
        "message": message,
        "expected": expected,
        "found": found,
        "missing": missing,
        "found_count": len(found),
    }


def _validate_downloader_root(root: Path) -> dict[str, Any]:
    if root.exists() and not root.is_dir():
        return {
            "role": "downloader",
            "path": str(root),
            "status": "error",
            "message": "download root exists but is not a folder",
            "can_write": False,
            "can_create": False,
        }

    if root.exists():
        can_write = os.access(root, os.W_OK)
        return {
            "role": "downloader",
            "path": str(root),
            "status": "ok" if can_write else "error",
            "message": "download root is writable" if can_write else "download root is not writable",
            "can_write": can_write,
            "can_create": False,
        }

    parent = _nearest_existing_parent(root)
    can_create = bool(parent and os.access(parent, os.W_OK))
    return {
        "role": "downloader",
        "path": str(root),
        "status": "warning" if can_create else "error",
        "message": "download root can be created" if can_create else "download root cannot be created",
        "can_write": False,
        "can_create": can_create,
        "existing_parent": str(parent) if parent else None,
    }


def _nearest_existing_parent(path: Path) -> Path | None:
    current = path
    while current != current.parent:
        current = current.parent
        if current.exists():
            return current if current.is_dir() else None
    return current if current.exists() and current.is_dir() else None


def _is_hidden_or_system(path: Path) -> bool:
    name = path.name
    if name.startswith("."):
        return True
    attrs = getattr(path.stat(), "st_file_attributes", 0)
    return bool(attrs & (WINDOWS_HIDDEN | WINDOWS_SYSTEM))
