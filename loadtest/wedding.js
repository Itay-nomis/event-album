// k6 load test: a 300-guest wedding.
//
// Every guest uploads ~3 photos over ~10 minutes -> ~900 uploads, with a spike in the
// middle (first dance: everybody takes out their phone at once).
// One k6 "iteration" = one guest uploading one photo.
//
// Run with run-loadtest.ps1 (uses the grafana/k6 Docker image, nothing to install).
import http from "k6/http";
import { check, sleep } from "k6";
import { Counter, Trend } from "k6/metrics";

const BASE_URL = (__ENV.BASE_URL || "http://host.docker.internal:8080").replace(/\/$/, "");
const SPEED = Number(__ENV.SPEED || 1); // 2 = twice as many uploads per minute
const TIME_SCALE = Number(__ENV.TIME_SCALE || 1); // 0.1 = squeeze the 10 minutes into 1 (smoke test)

// Test images are read once at startup (init context), then reused
const IMAGE_NAMES = JSON.parse(open("./images/index.json"));
const IMAGES = IMAGE_NAMES.map((name) => ({ name, data: open(`./images/${name}`, "b") }));

const uploads = new Counter("album_uploads_ok");
const uploadTime = new Trend("album_upload_duration", true);

// Uploads per minute over the evening (timeUnit 1m). Sum is roughly 900.
const r = (perMinute) => Math.max(1, Math.round(perMinute * SPEED));
const d = (minutes) => `${Math.max(5, Math.round(minutes * 60 * TIME_SCALE))}s`;
export const options = {
  scenarios: {
    wedding: {
      executor: "ramping-arrival-rate",
      startRate: r(10),
      timeUnit: "1m",
      preAllocatedVUs: 20,
      maxVUs: 150,
      stages: [
        { duration: d(2), target: r(60) }, // guests arrive, first photos
        { duration: d(2), target: r(60) }, // steady
        { duration: d(1), target: r(240) }, // first dance!
        { duration: d(1), target: r(240) }, // spike holds
        { duration: d(1), target: r(60) }, // back to the tables
        { duration: d(3), target: r(20) }, // winding down
      ],
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"], // fewer than 1% errors
    album_upload_duration: ["p(95)<5000"], // 95% of uploads accepted within 5s
  },
};

export function setup() {
  const res = http.post(`${BASE_URL}/api/events`, JSON.stringify({ name: "Load test wedding" }), {
    headers: { "Content-Type": "application/json" },
  });
  check(res, { "event created": (r) => r.status === 200 });
  if (res.status !== 200) {
    throw new Error(`Cannot create event at ${BASE_URL}: HTTP ${res.status}. Is the app running?`);
  }
  const code = res.json("code");
  console.log(`Event ${code} - watch it live: ${BASE_URL}/e/${code}/show`);
  return { code };
}

export default function (data) {
  const code = data.code;

  // A guest opens the upload page from the QR code (light requests)...
  http.get(`${BASE_URL}/e/${code}`, { tags: { name: "upload page" } });
  http.get(`${BASE_URL}/api/events/${code}`, { tags: { name: "event info" } });
  sleep(Math.random() * 2); // picks a photo

  // ...and uploads one photo
  const img = IMAGES[Math.floor(Math.random() * IMAGES.length)];
  const res = http.post(
    `${BASE_URL}/api/events/${code}/photos`,
    { file: http.file(img.data, img.name, "image/jpeg") },
    { tags: { name: "upload" }, timeout: "60s" },
  );
  const ok = check(res, { "upload accepted": (r) => r.status === 200 });
  if (ok) uploads.add(1);
  uploadTime.add(res.timings.duration);
}

export function teardown(data) {
  // The worker keeps processing after the last upload; give it a moment, then report
  sleep(30);
  const res = http.get(`${BASE_URL}/api/events/${data.code}/photos`);
  if (res.status === 200) {
    console.log(`Photos on the slideshow so far (sharp + processed): ${res.json().length}`);
  }
}
