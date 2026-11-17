"""Event Album API: events, photo uploads, live updates (SSE), media proxy."""

import asyncio
import io
import json
import logging
import os
import secrets
import string
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import qrcode
import qrcode.image.svg
import redis
import redis.asyncio as aioredis
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel

from app import db
from app import storage as storage_mod

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("api")

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
JOB_QUEUE = "jobs"  # Redis list the worker BLPOPs from (KEDA scales the worker on its length)
MAX_UPLOAD_MB = 25
# Optional: the public address guests use, e.g. https://album.example.com.
# When unset, it is worked out per request from the proxy headers (see public_base_url).
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
STATIC = Path(__file__).parent / "static"

# ---------- clients (created lazily, so tests can swap in fakes) ----------
_storage: storage_mod.Storage | None = None
_redis: redis.Redis | None = None


def get_storage() -> storage_mod.Storage:
    global _storage
    if _storage is None:
        _storage = storage_mod.from_env()
    return _storage


def get_redis() -> redis.Redis:
    global _redis
    if _redis is None:
        _redis = redis.Redis.from_url(REDIS_URL, socket_timeout=5)
    return _redis


# ---------- metrics (scraped by Prometheus at /metrics) ----------
REQUESTS = Counter("album_http_requests_total", "HTTP requests", ["method", "route", "status"])
LATENCY = Histogram(
    "album_http_request_duration_seconds",
    "Time until the response headers are sent",
    ["method", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
UPLOADS = Counter("album_uploads_total", "Photos accepted for processing")


def _retry(what: str, fn, attempts: int = 30, delay: float = 2.0) -> None:
    """Dependencies may still be starting (compose / k8s start everything at once)."""
    for attempt in range(1, attempts + 1):
        try:
            fn()
            return
        except Exception as e:
            if attempt == attempts:
                raise
            log.warning("%s not ready (%s), retry %d/%d", what, e.__class__.__name__, attempt, attempts)
            time.sleep(delay)


def _init_db() -> None:
    with db.connect() as conn:
        db.init_schema(conn)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await asyncio.to_thread(_retry, "postgres", _init_db)
    await asyncio.to_thread(_retry, "storage", lambda: get_storage().ensure_container())
    yield


app = FastAPI(title="Event Album", lifespan=lifespan)


@app.middleware("http")
async def record_metrics(request: Request, call_next):
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        # Use the route template (/api/events/{code}) not the real path, to keep label count small
        route = request.scope.get("route")
        path = getattr(route, "path", "unmatched")
        if path != "/metrics":
            REQUESTS.labels(request.method, path, str(status)).inc()
            LATENCY.labels(request.method, path).observe(time.perf_counter() - start)


# ---------- health & metrics ----------
@app.get("/healthz")
def healthz():
    """Liveness: the process is up. Deliberately checks nothing else, so a DB outage
    does not make Kubernetes restart every api pod."""
    return {"ok": True}


@app.get("/readyz")
def readyz():
    """Readiness: only send traffic here when Postgres and Redis answer."""
    checks = {}
    for name, check in (("postgres", db.ping), ("redis", lambda: get_redis().ping())):
        try:
            check()
            checks[name] = "ok"
        except Exception as e:
            checks[name] = f"error: {e.__class__.__name__}"
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse({"ok": ok, "checks": checks}, status_code=200 if ok else 503)


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ---------- pages ----------
@app.get("/")
def home():
    return FileResponse(STATIC / "index.html")


@app.get("/e/{code}")
def upload_page(code: str):
    return FileResponse(STATIC / "upload.html")


@app.get("/e/{code}/show")
def show_page(code: str):
    return FileResponse(STATIC / "show.html")


# ---------- events ----------
class EventIn(BaseModel):
    name: str


def _new_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(6))


@app.post("/api/events")
def create_event(body: EventIn):
    name = body.name.strip()[:100] or "האירוע שלנו"
    code = _new_code()
    db.create_event(code, name)
    return {"code": code, "name": name}


def _get_event(code: str) -> dict:
    event = db.get_event(code.upper())
    if not event:
        raise HTTPException(404, "event not found")
    return event


@app.get("/api/events/{code}")
def get_event(code: str):
    return _get_event(code)


def _first(value: str | None) -> str | None:
    """Proxy headers can hold a list ("a, b"); the first entry is the original client's view."""
    return value.split(",")[0].strip() if value else None


def public_base_url(request: Request) -> str:
    """The address guests' phones should open (used for the QR code).

    1. PUBLIC_BASE_URL env var, if set (most reliable: use it for a real domain).
    2. Otherwise the proxy headers. Request path for the free Cloudflare quick tunnel:
         phone --https--> Cloudflare --> cloudflared (on the PC) --http--> Traefik / uvicorn
       cloudflared keeps the public Host (xyz.trycloudflare.com) and Cloudflare adds
       X-Forwarded-Proto and CF-Visitor {"scheme":"https"}. An http proxy in between (Traefik)
       may overwrite X-Forwarded-Proto with "http", so CF-Visitor wins when it is present.
    3. Otherwise the URL the request came in on.
    """
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
    headers = request.headers
    host = _first(headers.get("x-forwarded-host")) or headers.get("host") or request.url.netloc
    scheme = None
    try:
        scheme = json.loads(headers.get("cf-visitor", "{}")).get("scheme")
    except (ValueError, AttributeError):
        pass
    scheme = scheme or _first(headers.get("x-forwarded-proto")) or request.url.scheme
    return f"{scheme}://{host}"


@app.get("/api/events/{code}/qr")
def event_qr(code: str, request: Request):
    event = _get_event(code)
    url = f"{public_base_url(request)}/e/{event['code']}"
    img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=12)
    buf = io.BytesIO()
    img.save(buf)
    # The encoded URL is also sent as a header: handy for debugging and for tests.
    return Response(buf.getvalue(), media_type="image/svg+xml", headers={"X-QR-URL": url})


# ---------- photos ----------
@app.post("/api/events/{code}/photos")
async def upload_photo(code: str, file: UploadFile = File(...)):
    # This handler is async (for `await file.read()`), so every blocking call - Postgres,
    # S3, Redis - goes through asyncio.to_thread. A blocking call here would freeze the whole
    # event loop: every other upload and every open slideshow stream on this pod.
    event = await asyncio.to_thread(_get_event, code)
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(400, "only images are allowed")
    data = await file.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"max {MAX_UPLOAD_MB}MB")

    photo_id = uuid.uuid4()
    key = f"originals/{event['code']}/{photo_id}"
    job = json.dumps({"photo_id": str(photo_id), "event_code": event["code"]})
    await asyncio.to_thread(get_storage().put, key, data, file.content_type)
    await asyncio.to_thread(db.add_photo, photo_id, event["code"], key)
    # Hand the heavy work to the worker through a Redis queue
    await asyncio.to_thread(get_redis().rpush, JOB_QUEUE, job)
    UPLOADS.inc()
    return {"id": str(photo_id), "status": "pending"}


@app.get("/api/events/{code}/photos")
def list_photos(code: str):
    event = _get_event(code)
    return [{"id": pid, "url": f"/media/{key}"} for pid, key in db.list_ready_photos(event["code"])]


@app.get("/media/{key:path}")
def media(key: str):
    try:
        chunks, content_type = get_storage().stream(key)
    except storage_mod.NotFound:
        raise HTTPException(404) from None
    return StreamingResponse(
        chunks, media_type=content_type, headers={"Cache-Control": "public, max-age=86400"}
    )


@app.get("/api/events/{code}/stream")
async def stream(code: str, request: Request):
    """Server-Sent Events: pushes every newly processed photo to the slideshow."""
    event = await asyncio.to_thread(_get_event, code)

    async def gen():
        r = aioredis.from_url(REDIS_URL)
        pubsub = r.pubsub()
        await pubsub.subscribe(f"event:{event['code']}")
        try:
            while not await request.is_disconnected():
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15)
                if msg:
                    yield f"data: {msg['data'].decode()}\n\n"
                else:
                    # Comment line keeps proxies/tunnels from closing an idle connection
                    yield ": keepalive\n\n"
                await asyncio.sleep(0)
        finally:
            await pubsub.unsubscribe()
            await r.aclose()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        # Tell proxies (nginx-style X-Accel-Buffering, CDNs) not to buffer the stream
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
