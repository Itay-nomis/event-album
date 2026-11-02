"""Event Album API: events, photo uploads, live updates (SSE), media proxy."""
import asyncio
import io
import json
import os
import secrets
import string
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import boto3
import psycopg
import qrcode
import qrcode.image.svg
import redis
import redis.asyncio as aioredis
from botocore.exceptions import ClientError
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

DATABASE_URL = os.environ["DATABASE_URL"]
REDIS_URL = os.environ["REDIS_URL"]
S3_BUCKET = os.environ.get("S3_BUCKET", "photos")
MAX_UPLOAD_MB = 25
STATIC = Path(__file__).parent / "static"

s3 = boto3.client(
    "s3",
    endpoint_url=os.environ["S3_ENDPOINT"],
    aws_access_key_id=os.environ["S3_ACCESS_KEY"],
    aws_secret_access_key=os.environ["S3_SECRET_KEY"],
)
rds = redis.Redis.from_url(REDIS_URL)

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    code        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS photos (
    id           UUID PRIMARY KEY,
    event_code   TEXT NOT NULL REFERENCES events(code),
    original_key TEXT NOT NULL,
    display_key  TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',   -- pending | ready | failed
    blur_score   REAL,
    is_blurry    BOOLEAN,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS photos_event_idx ON photos(event_code, created_at);
"""


def db():
    return psycopg.connect(DATABASE_URL, autocommit=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    with db() as conn:
        conn.execute(SCHEMA)
    try:
        s3.head_bucket(Bucket=S3_BUCKET)
    except ClientError:
        s3.create_bucket(Bucket=S3_BUCKET)
    yield


app = FastAPI(title="Event Album", lifespan=lifespan)


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


@app.get("/healthz")
def healthz():
    return {"ok": True}


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
    with db() as conn:
        conn.execute("INSERT INTO events (code, name) VALUES (%s, %s)", (code, name))
    return {"code": code, "name": name}


def _get_event(code: str):
    with db() as conn:
        row = conn.execute("SELECT code, name FROM events WHERE code = %s", (code.upper(),)).fetchone()
    if not row:
        raise HTTPException(404, "event not found")
    return {"code": row[0], "name": row[1]}


@app.get("/api/events/{code}")
def get_event(code: str):
    return _get_event(code)


@app.get("/api/events/{code}/qr")
def event_qr(code: str, request: Request):
    event = _get_event(code)
    url = f"{request.base_url}e/{event['code']}"
    img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=12)
    buf = io.BytesIO()
    img.save(buf)
    return Response(buf.getvalue(), media_type="image/svg+xml")


# ---------- photos ----------
@app.post("/api/events/{code}/photos")
async def upload_photo(code: str, file: UploadFile = File(...)):
    event = _get_event(code)
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(400, "only images are allowed")
    data = await file.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"max {MAX_UPLOAD_MB}MB")

    photo_id = uuid.uuid4()
    key = f"originals/{event['code']}/{photo_id}"
    s3.put_object(Bucket=S3_BUCKET, Key=key, Body=data, ContentType=file.content_type)
    with db() as conn:
        conn.execute(
            "INSERT INTO photos (id, event_code, original_key) VALUES (%s, %s, %s)",
            (photo_id, event["code"], key),
        )
    # Hand the heavy work to the worker through a Redis queue
    rds.rpush("jobs", json.dumps({"photo_id": str(photo_id), "event_code": event["code"]}))
    return {"id": str(photo_id), "status": "pending"}


@app.get("/api/events/{code}/photos")
def list_photos(code: str):
    event = _get_event(code)
    with db() as conn:
        rows = conn.execute(
            """SELECT id, display_key FROM photos
               WHERE event_code = %s AND status = 'ready' AND NOT is_blurry
               ORDER BY created_at""",
            (event["code"],),
        ).fetchall()
    return [{"id": str(r[0]), "url": f"/media/{r[1]}"} for r in rows]


@app.get("/media/{key:path}")
def media(key: str):
    try:
        obj = s3.get_object(Bucket=S3_BUCKET, Key=key)
    except ClientError:
        raise HTTPException(404)
    return StreamingResponse(
        obj["Body"].iter_chunks(),
        media_type=obj.get("ContentType", "image/jpeg"),
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/api/events/{code}/stream")
async def stream(code: str, request: Request):
    """Server-Sent Events: pushes every newly processed photo to the slideshow."""
    event = _get_event(code)

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
                    yield ": keepalive\n\n"
                await asyncio.sleep(0)
        finally:
            await pubsub.unsubscribe()
            await r.aclose()

    return StreamingResponse(gen(), media_type="text/event-stream")
