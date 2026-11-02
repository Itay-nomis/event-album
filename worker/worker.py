"""Background worker: takes upload jobs from Redis, processes the image, publishes it live."""
import io
import json
import logging
import os
import time

import boto3
import psycopg
import redis
from PIL import Image, ImageFilter, ImageOps, ImageStat
from pillow_heif import register_heif_opener

register_heif_opener()  # iPhone HEIC support
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("worker")

DATABASE_URL = os.environ["DATABASE_URL"]
S3_BUCKET = os.environ.get("S3_BUCKET", "photos")
MAX_SIDE = 1600
BLUR_THRESHOLD = float(os.environ.get("BLUR_THRESHOLD", "60"))

s3 = boto3.client(
    "s3",
    endpoint_url=os.environ["S3_ENDPOINT"],
    aws_access_key_id=os.environ["S3_ACCESS_KEY"],
    aws_secret_access_key=os.environ["S3_SECRET_KEY"],
)
rds = redis.Redis.from_url(os.environ["REDIS_URL"])


def blur_score(img: Image.Image) -> float:
    """Edge variance on a small grayscale copy. Low value = few sharp edges = blurry."""
    small = img.convert("L").resize((400, int(400 * img.height / img.width)))
    edges = small.filter(ImageFilter.FIND_EDGES)
    return ImageStat.Stat(edges).var[0]


def process(job: dict) -> None:
    photo_id, code = job["photo_id"], job["event_code"]
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        row = conn.execute("SELECT original_key FROM photos WHERE id = %s", (photo_id,)).fetchone()
        if not row:
            log.warning("photo %s not found, skipping", photo_id)
            return
        try:
            raw = s3.get_object(Bucket=S3_BUCKET, Key=row[0])["Body"].read()
            img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
            img.thumbnail((MAX_SIDE, MAX_SIDE))
            score = blur_score(img)
            blurry = score < BLUR_THRESHOLD

            out = io.BytesIO()
            img.save(out, "JPEG", quality=85, optimize=True)
            display_key = f"display/{code}/{photo_id}.jpg"
            s3.put_object(Bucket=S3_BUCKET, Key=display_key, Body=out.getvalue(), ContentType="image/jpeg")

            conn.execute(
                """UPDATE photos SET status='ready', display_key=%s, blur_score=%s, is_blurry=%s
                   WHERE id=%s""",
                (display_key, score, blurry, photo_id),
            )
            log.info("photo %s done (blur=%.1f%s)", photo_id, score, ", BLURRY" if blurry else "")
            if not blurry:
                rds.publish(f"event:{code}", json.dumps({"id": photo_id, "url": f"/media/{display_key}"}))
        except Exception:
            log.exception("failed processing %s", photo_id)
            conn.execute("UPDATE photos SET status='failed' WHERE id=%s", (photo_id,))


def main() -> None:
    log.info("worker started, waiting for jobs")
    while True:
        try:
            item = rds.blpop("jobs", timeout=5)
            if item:
                process(json.loads(item[1]))
        except redis.ConnectionError:
            log.warning("redis unavailable, retrying")
            time.sleep(2)


if __name__ == "__main__":
    main()
