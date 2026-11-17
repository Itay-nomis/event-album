"""Shared fixtures: the API runs against in-memory fakes, no Postgres/Redis/S3 needed."""

import os

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import fakeredis  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db, main  # noqa: E402
from app.storage import NotFound  # noqa: E402


class FakeStorage:
    def __init__(self):
        self.objects: dict[str, tuple[bytes, str]] = {}

    def ensure_container(self):
        pass

    def put(self, key, data, content_type):
        self.objects[key] = (data, content_type)

    def get(self, key):
        if key not in self.objects:
            raise NotFound(key)
        return self.objects[key][0]

    def stream(self, key):
        data, content_type = self.objects.get(key) or (None, None)
        if data is None:
            raise NotFound(key)
        return iter([data]), content_type


class FakeDB:
    """Replaces the functions in app/db.py with dict-backed versions."""

    def __init__(self):
        self.events: dict[str, str] = {}
        self.photos: list[dict] = []
        self.healthy = True

    def install(self, monkeypatch):
        monkeypatch.setattr(db, "ping", self.ping)
        monkeypatch.setattr(db, "create_event", self.create_event)
        monkeypatch.setattr(db, "get_event", self.get_event)
        monkeypatch.setattr(db, "add_photo", self.add_photo)
        monkeypatch.setattr(db, "list_ready_photos", self.list_ready_photos)

    def ping(self):
        if not self.healthy:
            raise ConnectionError("db down")

    def create_event(self, code, name):
        self.events[code] = name

    def get_event(self, code):
        return {"code": code, "name": self.events[code]} if code in self.events else None

    def add_photo(self, photo_id, event_code, original_key):
        self.photos.append({"id": str(photo_id), "event_code": event_code, "original_key": original_key})

    def list_ready_photos(self, event_code):
        return [(p["id"], p["display_key"]) for p in self.photos if p.get("display_key")]


@pytest.fixture
def fake_db(monkeypatch):
    fake = FakeDB()
    fake.install(monkeypatch)
    return fake


@pytest.fixture
def fake_storage(monkeypatch):
    fake = FakeStorage()
    monkeypatch.setattr(main, "_storage", fake)
    return fake


@pytest.fixture
def fake_redis(monkeypatch):
    fake = fakeredis.FakeRedis()
    monkeypatch.setattr(main, "_redis", fake)
    return fake


@pytest.fixture
def client(fake_db, fake_storage, fake_redis):
    # No `with TestClient(...)`: that would run the startup hook, which connects to Postgres.
    return TestClient(main.app)
