"""Private document storage. Local filesystem backend with generated names; storage "",
    the adapter interface matches a Supabase Storage backend."""
import hashlib
import os
import re
import uuid
from pathlib import Path

from app.config import get_settings


class StorageError(Exception):
    pass


_MAGIC_BYTES: dict[str, list[bytes]] = {
    "pdf": [b"%PDF-"],
    "png": [b"\x89PNG\r\n\x1a\n"],
    "jpeg": [b"\xff\xd8\xff"],
}

MIME_BY_EXT = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}

ALLOWED_EXTENSIONS = set(MIME_BY_EXT)


def detect_file_ext(data: bytes) -> str:
    for ext, magics in _MAGIC_BYTES.items():
        if any(data.startswith(magic) for magic in magics):
            return ext
    raise StorageError("File content does not match an allowed type (PDF, PNG, JPEG)")


def sanitize_storage_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value)[:64]


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_application_file(application_ref: str, data: bytes, declared_filename: str) -> dict:
    """Stores bytes privately; returns storage metadata. Never trusts client filenames."""
    settings = get_settings()
    actual_ext = detect_file_ext(data)
    safe_ref = sanitize_storage_part(application_ref)
    safe_name = f"{uuid.uuid4().hex[:12]}_{sanitize_storage_part(Path(declared_filename).stem)}.{actual_ext}"
    rel_path = f"{safe_ref}/{date_dir()}/{safe_name}"
    root = settings.root_path(settings.STORAGE_ROOT)
    dest = root / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    tmp.write_bytes(data)
    os.replace(tmp, dest)
    return {
        "backend": settings.STORAGE_BACKEND,
        "path": rel_path,
        "mime_type": MIME_BY_EXT[f".{actual_ext}"] if actual_ext == "jpeg" and declared_filename.lower().endswith(("jpg", "jpeg")) else MIME_BY_EXT[f".{actual_ext}"],
        "file_ext": actual_ext,
        "size_bytes": len(data),
        "sha256": sha256_hex(data),
    }


def date_dir() -> str:
    from datetime import date

    return date.today().strftime("%Y%m")


def resolve(storage_backend: str, storage_path: str) -> Path:
    settings = get_settings()
    if storage_backend == "supabase":
        raise StorageError("Supabase Storage backend not configured on this instance")
    root = settings.root_path(settings.STORAGE_ROOT)
    candidate = (root / storage_path).resolve()
    if not str(candidate).startswith(str(root.resolve())):
        raise StorageError("Path traversal rejected")
    if not candidate.is_file():
        raise StorageError("Stored file missing; it may have been moved")
    return candidate


def read_file(storage_backend: str, storage_path: str) -> bytes:
    return resolve(storage_backend, storage_path).read_bytes()


def save_report_file(application_ref: str, data: bytes, name_hint: str) -> dict:
    safe_ref = sanitize_storage_part(application_ref)
    rel_path = f"reports/{safe_ref}/{date_dir()}/{uuid.uuid4().hex[:10]}_{sanitize_storage_part(name_hint)}.pdf"
    root = get_settings().root_path(get_settings().REPORT_ROOT)
    dest = root / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    tmp.write_bytes(data)
    os.replace(tmp, dest)
    return {
        "backend": get_settings().STORAGE_BACKEND,
        "path": rel_path,
        "sha256": sha256_hex(data),
        "size_bytes": len(data),
    }


def resolve_report(storage_backend: str, storage_path: str) -> Path:
    settings = get_settings()
    root = settings.root_path(settings.REPORT_ROOT)
    candidate = (root / storage_path).resolve()
    if not str(candidate).startswith(str(root.resolve())):
        raise StorageError("Path traversal rejected")
    if not candidate.is_file():
        raise StorageError("Report file missing")
    return candidate
