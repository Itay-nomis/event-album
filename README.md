# 📸 Event Album

Live photo album for events. Guests scan a QR code, upload photos from their phone (no app install),
and the photos appear on the venue screen in real time.

This is a hands-on DevOps learning project: the app is the workload, the infrastructure is the point.

## Architecture

```
 Guest phone ──upload──► API (FastAPI) ──original──► Object storage (MinIO / S3)
                           │    │
                           │    └──job──► Redis queue ──► Worker (resize, blur filter)
                           │                                   │
                           ▼                                   ▼
                       Postgres ◄──────── status ──────── publish "new photo"
                                                               │
 Venue screen ◄────── live updates (SSE) ◄──── Redis pub/sub ◄─┘
```

| Service    | Role                                             |
|------------|--------------------------------------------------|
| `api`      | Pages, uploads, event data, live stream, media   |
| `worker`   | Processes images in the background               |
| `postgres` | Events and photo metadata                        |
| `redis`    | Job queue + pub/sub for live updates             |
| `minio`    | S3-compatible storage for the image files        |

## Run locally

```bash
cp .env.example .env          # Windows PowerShell: copy .env.example .env
docker compose up --build
```

- App: http://localhost:8000
- MinIO console: http://localhost:9001 (credentials from `.env`)

To test from a phone on the same Wi-Fi, open `http://<your-PC-LAN-IP>:8000` on the PC when creating the event,
so the QR code points at an address the phone can reach.

## Roadmap

- [x] **1. Local** — app runs with Docker Compose
- [ ] **2. CI** — GitHub Actions: lint, test, build and push images
- [ ] **3. Cloud** — infrastructure with Terraform
- [ ] **4. Kubernetes + GitOps** — deploy with ArgoCD
- [ ] **5. Scale** — workers autoscale on queue length; load test a 300-guest wedding
- [ ] **6. Observability** — Prometheus, Grafana, alerts
- [ ] **7. Bonus** — AI photo curation, incident-analysis bot
