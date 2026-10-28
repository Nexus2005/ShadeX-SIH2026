import pytest

from app.config import get_settings
from app.services import storage


PDF_BYTES = b"%PDF-1.7\nminimal"
PNG_BYTES = b"\x89PNG\r\n\x1a\nrest"
JPEG_BYTES = b"\xff\xd8\xff\xe0rest"


def test_detect_file_ext_by_magic_bytes():
    assert storage.detect_file_ext(PDF_BYTES) == "pdf"
    assert storage.detect_file_ext(PNG_BYTES) == "png"
    assert storage.detect_file_ext(JPEG_BYTES) == "jpeg"


def test_detect_file_ext_rejects_executables_and_text():
    with pytest.raises(storage.StorageError):
        storage.detect_file_ext(b"MZ\x90\x00windows executable")
    with pytest.raises(storage.StorageError):
        storage.detect_file_ext(b"just some text pretending to be a pdf")


def test_sanitize_storage_part_strips_separators():
    assert storage.sanitize_storage_part("../../etc/passwd") == "_etc_passwd"
    assert storage.sanitize_storage_part("RAI-8C14CE4E") == "RAI-8C14CE4E"
    assert "/" not in storage.sanitize_storage_part("a/b")


def test_sha256_hex_is_stable():
    assert storage.sha256_hex(b"abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_save_application_file_generates_name_and_ignores_declared_path(tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "STORAGE_ROOT", str(tmp_path))

    meta = storage.save_application_file("RAI-TEST", PDF_BYTES, "../../evil name.pdf")

    assert meta["sha256"] == storage.sha256_hex(PDF_BYTES)
    assert meta["file_ext"] == "pdf"
    assert meta["mime_type"] == "application/pdf"
    assert meta["size_bytes"] == len(PDF_BYTES)
    assert ".." not in meta["path"]
    assert storage.resolve("local", meta["path"]).read_bytes() == PDF_BYTES
    assert (tmp_path / "RAI-TEST").is_dir()


def test_save_application_file_rejects_non_document(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "STORAGE_ROOT", str(tmp_path))
    with pytest.raises(storage.StorageError):
        storage.save_application_file("RAI-TEST", b"not a document", "doc.pdf")
    assert not list(tmp_path.rglob("*"))


def test_resolve_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "STORAGE_ROOT", str(tmp_path))
    (tmp_path / "ok.pdf").write_bytes(PDF_BYTES)
    outside = tmp_path.parent / "outside.pdf"
    outside.write_bytes(PDF_BYTES)
    try:
        with pytest.raises(storage.StorageError):
            storage.resolve("local", "../outside.pdf")
    finally:
        outside.unlink(missing_ok=True)


def test_resolve_missing_file_reports_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "STORAGE_ROOT", str(tmp_path))
    with pytest.raises(storage.StorageError):
        storage.resolve("local", "nope/missing.pdf")
