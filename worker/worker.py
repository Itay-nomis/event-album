"""Background worker: takes upload jobs from Redis, processes the image, publishes it live."""

import io
import json
import logging
import os
import signal
import threading
import time

import psycopg
import redis
from PIL import Image, ImageFilter, ImageOps, ImageStat
from pillow_heif import register_heif_opener
from prometheus_client import Counter, Gauge, Histogram, start_http_server

import storage as storage_mod

register_heif_opener()  # iPhone HEIC support
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("worker")

QUEUE = "jobs"
MAX_SIDE = 1600
BLUR_THRESHOLD = float(os.environ.get("BLUR_THRESHOLD", "60"))
METRICS_PORT = int(os.environ.get("METRICS_PORT", "9100"))

# ---------- metrics (Prometheus scrapes http://<pod>:9100/metrics) ----------
JOBS_PROCESSED = Counter("album_worker_jobs_processed_total", "Jobs finished successfully", ["result"])
JOBS_FAILED = Counter("album_worker_jobs_failed_total", "Jobs that raised an error")
PROCESSING_SECONDS = Histogram(
    "album_worker_processing_seconds",
    "Time to process one photo",
    buckets=(0.1, 0.25, 0.5, 1, 2, 3, 5, 8, 13, 20, 30),
)
QUEUE_LENGTH = Gauge("album_worker_queue_length", "Jobs waiting in the Redis list")

# Set by SIGTERM/SIGINT. Kubernetes sends SIGTERM when it removes a pod (e.g. KEDA scaling
# down); we then finish the photo in hand and exit instead of dropping it half-done.
STOP = threading.Event()


def request_stop(signum=None, _frame=None) -> None:
    log.info("got signal %s: finishing current job, then exiting", signum)
    STOP.set()


# ---------- image processing (pure functions, easy to test) ----------
def blur_score(img: Image.Image) -> float:
    """Edge variance on a small grayscale copy. Low value = few sharp edges = blurry."""
    small = img.convert("L").resize((400, max(1, int(400 * img.height / img.width))))
    edges = small.filter(ImageFilter.FIND_EDGES)
    # FIND_EDGES leaves the 1px border unfiltered (raw pixel values), which would add fake
    # "edges" to every photo - so measure only the inside.
    edges = edges.crop((1, 1, edges.width - 1, edges.height - 1))
    return ImageStat.Stat(edges).var[0]


def process_image(raw: bytes) -> tuple[bytes, float]:
    """Original upload bytes -> (display JPEG bytes, blur score)."""
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    score = blur_score(img)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85, optimize=True)
    return out.getvalue(), score


# ---------- one job ----------
def process(job: dict, conn, store: storage_mod.Storage, rds: redis.Redis) -> str:
    """Process one upload job. Returns the outcome: ready | blurry | failed | missing."""
    photo_id, code = job["photo_id"], job["event_code"]
    row = conn.execute("SELECT original_key FROM photos WHERE id = %s", (photo_id,)).fetchone()
    if not row:
        log.warning("photo %s not found, skipping", photo_id)
        return "missing"
    try:
        display, score = process_image(store.get(row[0]))
        blurry = score < BLUR_THRESHOLD
        display_key = f"display/{code}/{photo_id}.jpg"
        store.put(display_key, display, "image/jpeg")
        conn.execute(
            """UPDATE photos SET status='ready', display_key=%s, blur_score=%s, is_blurry=%s
               WHERE id=%s""",
            (display_key, score, blurry, photo_id),
        )
        log.info("photo %s done (blur=%.1f%s)", photo_id, score, ", BLURRY" if blurry else "")
        if not blurry:
            rds.publish(f"event:{code}", json.dumps({"id": photo_id, "url": f"/media/{display_key}"}))
        return "blurry" if blurry else "ready"
    except Exception:
        log.exception("failed processing %s", photo_id)
        conn.execute("UPDATE photos SET status='failed' WHERE id=%s", (photo_id,))
        return "failed"


def handle(raw_job: bytes, store: storage_mod.Storage, rds: redis.Redis, connect) -> str:
    """Process one job with metrics around it."""
    start = time.perf_counter()
    try:
        with connect() as conn:
            result = process(json.loads(raw_job), conn, store, rds)
    except Exception:
        log.exception("job crashed: %r", raw_job[:200])
        result = "failed"
    if result == "failed":
        JOBS_FAILED.inc()
    else:
        JOBS_PROCESSED.labels(result).inc()
    PROCESSING_SECONDS.observe(time.perf_counter() - start)
    return result


def run(store: storage_mod.Storage, rds: redis.Redis, connect) -> None:
    """Main loop. Checks STOP between jobs, never in the middle of one."""
    log.info("worker started, waiting for jobs")
    while not STOP.is_set():
        try:
            QUEUE_LENGTH.set(rds.llen(QUEUE))
            # Short timeout so a SIGTERM is noticed within ~2s when the queue is empty
            item = rds.blpop(QUEUE, timeout=2)
            if item:
                handle(item[1], store, rds, connect)
        except redis.ConnectionError:
            log.warning("redis unavailable, retrying")
            STOP.wait(2)
    log.info("worker stopped cleanly")


def main() -> None:
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    start_http_server(METRICS_PORT)
    store = storage_mod.from_env()
    rds = redis.Redis.from_url(os.environ["REDIS_URL"], socket_timeout=10)
    database_url = os.environ["DATABASE_URL"]
    run(store, rds, lambda: psycopg.connect(database_url, autocommit=True, connect_timeout=5))


if __name__ == "__main__":
    main()
