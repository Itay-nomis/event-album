import io
import json

from starlette.requests import Request

from app import main


def _png_bytes() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, "PNG")
    return buf.getvalue()


def _make_event(client) -> str:
    res = client.post("/api/events", json={"name": "Adi & Itay"})
    assert res.status_code == 200
    return res.json()["code"]


def test_healthz(client):
    assert client.get("/healthz").json() == {"ok": True}


def test_readyz_ok(client):
    res = client.get("/readyz")
    assert res.status_code == 200
    assert res.json()["checks"] == {"postgres": "ok", "redis": "ok"}


def test_readyz_fails_when_db_down(client, fake_db):
    fake_db.healthy = False
    res = client.get("/readyz")
    assert res.status_code == 503
    assert res.json()["checks"]["postgres"].startswith("error")


def test_create_and_get_event(client):
    code = _make_event(client)
    assert len(code) == 6
    assert client.get(f"/api/events/{code.lower()}").json() == {"code": code, "name": "Adi & Itay"}


def test_empty_name_gets_default(client):
    res = client.post("/api/events", json={"name": "   "})
    assert res.json()["name"] == "האירוע שלנו"


def test_unknown_event_404(client):
    assert client.get("/api/events/NOPE00").status_code == 404


def test_upload_stores_file_and_queues_job(client, fake_storage, fake_redis, fake_db):
    code = _make_event(client)
    before = main.UPLOADS._value.get()
    res = client.post(f"/api/events/{code}/photos", files={"file": ("a.png", _png_bytes(), "image/png")})
    assert res.status_code == 200
    photo_id = res.json()["id"]

    key = f"originals/{code}/{photo_id}"
    assert key in fake_storage.objects
    assert fake_db.photos[0]["original_key"] == key
    job = json.loads(fake_redis.lpop("jobs"))
    assert job == {"photo_id": photo_id, "event_code": code}
    assert main.UPLOADS._value.get() == before + 1


def test_upload_rejects_non_images(client, fake_redis):
    code = _make_event(client)
    res = client.post(f"/api/events/{code}/photos", files={"file": ("a.txt", b"hello", "text/plain")})
    assert res.status_code == 400
    assert fake_redis.llen("jobs") == 0


def test_list_photos_builds_media_urls(client, fake_db):
    code = _make_event(client)
    fake_db.photos.append({"id": "p1", "display_key": f"display/{code}/p1.jpg"})
    assert client.get(f"/api/events/{code}/photos").json() == [
        {"id": "p1", "url": f"/media/display/{code}/p1.jpg"}
    ]


def test_media_proxy(client, fake_storage):
    fake_storage.put("display/X/1.jpg", b"jpegdata", "image/jpeg")
    res = client.get("/media/display/X/1.jpg")
    assert res.status_code == 200
    assert res.content == b"jpegdata"
    assert res.headers["content-type"] == "image/jpeg"
    assert client.get("/media/display/X/missing.jpg").status_code == 404


def test_qr_uses_tunnel_host_and_https(client):
    """What cloudflared + Traefik send: public Host, CF-Visitor https, X-Forwarded-Proto http."""
    code = _make_event(client)
    res = client.get(
        f"/api/events/{code}/qr",
        headers={
            "host": "funny-words.trycloudflare.com",
            "x-forwarded-proto": "http",
            "cf-visitor": '{"scheme":"https"}',
        },
    )
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("image/svg+xml")
    assert res.headers["x-qr-url"] == f"https://funny-words.trycloudflare.com/e/{code}"


def test_qr_public_base_url_env_wins(client, monkeypatch):
    monkeypatch.setattr(main, "PUBLIC_BASE_URL", "https://album.example.com")
    code = _make_event(client)
    res = client.get(f"/api/events/{code}/qr", headers={"host": "localhost:8080"})
    assert res.headers["x-qr-url"] == f"https://album.example.com/e/{code}"


def _request(headers: dict, scheme="http", server=("localhost", 8000)) -> Request:
    return Request(
        {
            "type": "http",
            "scheme": scheme,
            "server": server,
            "path": "/",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        }
    )


def test_public_base_url_forwarded_headers():
    req = _request({"host": "10.0.0.5:8000", "x-forwarded-host": "album.lan", "x-forwarded-proto": "https"})
    assert main.public_base_url(req) == "https://album.lan"


def test_public_base_url_falls_back_to_request():
    assert main.public_base_url(_request({"host": "192.168.1.20:8000"})) == "http://192.168.1.20:8000"


def test_public_base_url_ignores_bad_cf_visitor():
    req = _request({"host": "a.example", "cf-visitor": "not-json", "x-forwarded-proto": "https"})
    assert main.public_base_url(req) == "https://a.example"


def test_sse_headers_disable_buffering(client, monkeypatch):
    """The stream endpoint itself needs real Redis pub/sub; check the response headers only."""
    code = _make_event(client)

    async def fake_disconnected(self):
        return True  # end the stream immediately

    monkeypatch.setattr(Request, "is_disconnected", fake_disconnected)

    closed = []

    class FakePubSub:
        async def subscribe(self, *_):
            pass

        async def unsubscribe(self, *_):
            raise ConnectionError("connection already gone")  # client hung up mid-read

        async def aclose(self):
            closed.append("pubsub")

    class FakeAsyncRedis:
        def pubsub(self):
            return FakePubSub()

        async def aclose(self):
            closed.append("redis")

    monkeypatch.setattr(main.aioredis, "from_url", lambda *_a, **_k: FakeAsyncRedis())
    res = client.get(f"/api/events/{code}/stream")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    assert res.headers["cache-control"] == "no-cache"
    assert res.headers["x-accel-buffering"] == "no"
    assert closed == ["pubsub", "redis"]  # Redis connections released even if unsubscribe fails


def test_metrics_endpoint(client):
    client.get("/healthz")
    body = client.get("/metrics").text
    assert 'album_http_requests_total{method="GET",route="/healthz",status="200"}' in body
    assert "album_http_request_duration_seconds_bucket" in body
    assert "album_uploads_total" in body


def test_upload_does_not_block_the_event_loop(client, fake_db, fake_redis, monkeypatch):
    """Postgres/Redis calls in the async upload handler must run in a worker thread, not on
    the event loop (a blocking call there stalls every other request and SSE stream)."""
    import asyncio

    def on_event_loop() -> bool:
        try:
            asyncio.get_running_loop()
            return True
        except RuntimeError:
            return False

    calls = []
    real_get_event, real_add_photo, real_rpush = fake_db.get_event, fake_db.add_photo, fake_redis.rpush

    def get_event(code):
        calls.append(("get_event", on_event_loop()))
        return real_get_event(code)

    def add_photo(*args):
        calls.append(("add_photo", on_event_loop()))
        return real_add_photo(*args)

    def rpush(*args):
        calls.append(("rpush", on_event_loop()))
        return real_rpush(*args)

    code = _make_event(client)
    monkeypatch.setattr(main.db, "get_event", get_event)
    monkeypatch.setattr(main.db, "add_photo", add_photo)
    monkeypatch.setattr(fake_redis, "rpush", rpush)
    res = client.post(f"/api/events/{code}/photos", files={"file": ("a.png", _png_bytes(), "image/png")})
    assert res.status_code == 200
    assert calls == [("get_event", False), ("add_photo", False), ("rpush", False)]
