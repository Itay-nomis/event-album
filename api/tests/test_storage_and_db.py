from pathlib import Path
from unittest import mock

import pytest
from moto import mock_aws

from app import db
from app import storage as storage_mod

REPO = Path(__file__).resolve().parents[2]


def test_storage_module_copies_are_identical():
    """api/app/storage.py and worker/storage.py must stay in sync."""
    assert (REPO / "api/app/storage.py").read_text() == (REPO / "worker/storage.py").read_text()


@mock_aws
def test_s3_storage_roundtrip(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_BUCKET", "photos")
    monkeypatch.setenv("S3_ACCESS_KEY", "test")
    monkeypatch.setenv("S3_SECRET_KEY", "test")
    monkeypatch.delenv("S3_ENDPOINT", raising=False)
    store = storage_mod.from_env()
    store.ensure_container()
    store.ensure_container()  # second call must be a no-op

    store.put("a/b.jpg", b"hello", "image/jpeg")
    assert store.get("a/b.jpg") == b"hello"
    chunks, content_type = store.stream("a/b.jpg")
    assert b"".join(chunks) == b"hello"
    assert content_type == "image/jpeg"
    with pytest.raises(storage_mod.NotFound):
        store.get("missing")


def test_azure_storage_uses_blob_client():
    from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError

    with mock.patch("azure.storage.blob.BlobServiceClient") as service_cls:
        container = service_cls.return_value.get_container_client.return_value
        store = storage_mod.AzureBlobStorage(
            "https://acct.blob.core.windows.net", "photos", credential=object()
        )

        container.create_container.side_effect = ResourceExistsError("exists")
        store.ensure_container()  # existing container is fine

        store.put("k", b"data", "image/png")
        kwargs = container.upload_blob.call_args.kwargs
        assert kwargs["name"] == "k" and kwargs["overwrite"] is True
        assert kwargs["content_settings"].content_type == "image/png"

        container.download_blob.return_value.readall.return_value = b"data"
        assert store.get("k") == b"data"

        container.download_blob.side_effect = ResourceNotFoundError("nope")
        with pytest.raises(storage_mod.NotFound):
            store.get("missing")


def test_unknown_backend(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "ftp")
    with pytest.raises(ValueError):
        storage_mod.from_env()


class RecordingConn:
    def __init__(self, fail_on_schema=False):
        self.sql: list[str] = []
        self.fail_on_schema = fail_on_schema

    def execute(self, sql, params=None):
        self.sql.append(sql.strip().split()[0] + (" " + sql.split("(")[0].split()[-1] if params else ""))
        if self.fail_on_schema and "CREATE TABLE" in sql:
            raise RuntimeError("boom")


def test_init_schema_takes_advisory_lock():
    conn = RecordingConn()
    db.init_schema(conn)
    assert conn.sql == ["SELECT pg_advisory_lock", "CREATE", "SELECT pg_advisory_unlock"]


def test_init_schema_releases_lock_on_error():
    conn = RecordingConn(fail_on_schema=True)
    with pytest.raises(RuntimeError):
        db.init_schema(conn)
    assert conn.sql[-1] == "SELECT pg_advisory_unlock"
