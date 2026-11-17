import io
import json
import os

os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import fakeredis  # noqa: E402
import pytest  # noqa: E402
from PIL import Image, ImageDraw, ImageFilter  # noqa: E402

import worker  # noqa: E402
from storage import NotFound  # noqa: E402


# ---------- test images ----------
def sharp_image(size=(800, 600)) -> Image.Image:
    """Checkerboard: lots of hard edges."""
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    step = 20
    for x in range(0, size[0], step):
        for y in range(0, size[1], step):
            if (x // step + y // step) % 2:
                draw.rectangle([x, y, x + step - 1, y + step - 1], fill="black")
    return img


def blurry_image(size=(800, 600)) -> Image.Image:
    return sharp_image(size).filter(ImageFilter.GaussianBlur(25))


def to_bytes(img: Image.Image, fmt="JPEG") -> bytes:
    buf = io.BytesIO()
    img.save(buf, fmt)
    return buf.getvalue()


# ---------- fakes ----------
class FakeStorage:
    def __init__(self):
        self.objects = {}

    def put(self, key, data, content_type):
        self.objects[key] = (data, content_type)

    def get(self, key):
        if key not in self.objects:
            raise NotFound(key)
        return self.objects[key][0]


class FakeConn:
    """Mimics the two queries the worker runs against the photos table."""

    def __init__(self, photos):
        self.photos = photos  # id -> dict(original_key=..., status=...)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        photo_id = params[-1]
        outer = self

        class Result:
            def fetchone(self):
                p = outer.photos.get(photo_id)
                return (p["original_key"],) if p else None

        if sql.startswith("SELECT"):
            return Result()
        if "status='ready'" in sql:
            display_key, score, blurry, _ = params
            self.photos[photo_id].update(status="ready", display_key=display_key, blur=score, blurry=blurry)
        elif "status='failed'" in sql:
            self.photos[photo_id]["status"] = "failed"
        return Result()


@pytest.fixture
def env():
    store = FakeStorage()
    conn = FakeConn({})
    rds = fakeredis.FakeRedis()
    return store, conn, rds


def add_photo(store, conn, photo_id, data):
    key = f"originals/EVT/{photo_id}"
    store.put(key, data, "image/jpeg")
    conn.photos[photo_id] = {"original_key": key, "status": "pending"}


# ---------- tests ----------
def test_blur_score_separates_sharp_from_blurry():
    sharp, blurry = worker.blur_score(sharp_image()), worker.blur_score(blurry_image())
    assert sharp > worker.BLUR_THRESHOLD > blurry


def test_process_image_resizes_to_jpeg():
    display, score = worker.process_image(to_bytes(sharp_image((4000, 3000)), "PNG"))
    out = Image.open(io.BytesIO(display))
    assert out.format == "JPEG"
    assert max(out.size) == worker.MAX_SIDE
    assert score > 0


def test_process_image_applies_exif_rotation():
    img = sharp_image((400, 200))
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation: rotate 90 degrees clockwise
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif)
    display, _ = worker.process_image(buf.getvalue())
    assert Image.open(io.BytesIO(display)).size == (200, 400)


def test_process_sharp_photo_is_published(env):
    store, conn, rds = env
    add_photo(store, conn, "p1", to_bytes(sharp_image()))
    pubsub = rds.pubsub()
    pubsub.subscribe("event:EVT")
    pubsub.get_message(timeout=1)  # subscribe confirmation

    assert worker.process({"photo_id": "p1", "event_code": "EVT"}, conn, store, rds) == "ready"
    assert conn.photos["p1"]["status"] == "ready"
    assert "display/EVT/p1.jpg" in store.objects
    msg = pubsub.get_message(timeout=1)
    assert json.loads(msg["data"]) == {"id": "p1", "url": "/media/display/EVT/p1.jpg"}


def test_process_blurry_photo_is_not_published(env):
    store, conn, rds = env
    add_photo(store, conn, "p2", to_bytes(blurry_image()))
    assert worker.process({"photo_id": "p2", "event_code": "EVT"}, conn, store, rds) == "blurry"
    assert conn.photos["p2"]["blurry"] is True


def test_process_corrupt_file_marks_failed(env):
    store, conn, rds = env
    add_photo(store, conn, "p3", b"definitely not an image")
    assert worker.process({"photo_id": "p3", "event_code": "EVT"}, conn, store, rds) == "failed"
    assert conn.photos["p3"]["status"] == "failed"


def test_process_missing_row_is_skipped(env):
    store, conn, rds = env
    assert worker.process({"photo_id": "nope", "event_code": "EVT"}, conn, store, rds) == "missing"


def test_handle_counts_metrics(env):
    store, conn, rds = env
    add_photo(store, conn, "p4", b"broken")
    failed_before = worker.JOBS_FAILED._value.get()
    job = json.dumps({"photo_id": "p4", "event_code": "EVT"}).encode()
    assert worker.handle(job, store, rds, lambda: conn) == "failed"
    assert worker.JOBS_FAILED._value.get() == failed_before + 1


def test_run_finishes_current_job_after_sigterm(env):
    """SIGTERM arrives while a job is in hand: the job completes, then the loop exits."""
    store, conn, rds = env
    add_photo(store, conn, "p5", to_bytes(sharp_image()))
    rds.rpush("jobs", json.dumps({"photo_id": "p5", "event_code": "EVT"}))
    rds.rpush("jobs", json.dumps({"photo_id": "never", "event_code": "EVT"}))

    real_blpop = rds.blpop

    def blpop_then_sigterm(*args, **kwargs):
        item = real_blpop(*args, **kwargs)
        worker.request_stop("SIGTERM")  # signal lands right after we took the job
        return item

    rds.blpop = blpop_then_sigterm
    worker.STOP.clear()
    try:
        worker.run(store, rds, lambda: conn)
    finally:
        worker.STOP.clear()

    assert conn.photos["p5"]["status"] == "ready"  # in-flight job finished
    assert rds.llen("jobs") == 1  # the next job was left for another worker
    assert worker.QUEUE_LENGTH._value.get() == 2  # gauge was set before taking the job
