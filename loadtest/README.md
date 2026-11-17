# Load test: a 300-guest wedding

`wedding.js` is a [k6](https://k6.io) script. Each guest uploads about 3 photos during a
10-minute evening (~900 uploads of 2-3 MB), with a spike in the middle - the first dance,
when everyone grabs their phone at once.

```
uploads/min
 240 |                 ████
     |                ██████
  60 |      ██████████      ██
  10 | ▁▃▅██                  ███▅▃▁
     +---------------------------------> 10 min
```

## Run it (PowerShell, from the repo root)

```powershell
.\loadtest\run-loadtest.ps1                 # full 10 minutes
.\loadtest\run-loadtest.ps1 -TimeScale 0.1  # 1-minute smoke test
.\loadtest\run-loadtest.ps1 -Speed 2        # a 600-guest wedding
.\loadtest\run-loadtest.ps1 -BaseUrl http://host.docker.internal:8000   # against docker compose
```

Everything runs in containers (`python` to generate the photos once, `grafana/k6` for the test),
so there is nothing to install. Generated photos go to `loadtest/images/` (gitignored); about 1 in 8 is
blurred on purpose, so the slideshow shows fewer photos than were uploaded.

## Watch the autoscaling

Open these in separate PowerShell windows before you start:

```powershell
kubectl -n event-album get pods -w          # workers appear (KEDA) and disappear afterwards
kubectl -n event-album get hpa -w           # keda-hpa-worker (queue) and api (CPU)
kubectl -n event-album get scaledobject     # READY/ACTIVE state of the KEDA trigger
```

And in Grafana (see the main README for the port-forward): dashboard **Event Album** ->
*Queue length vs worker replicas*. What you should see:

1. Queue length rises when uploads start; KEDA wakes the worker from **0 to 1** within seconds.
2. During the spike the queue grows faster than one worker drains it; KEDA adds workers
   (target: 5 waiting jobs per worker, max 10).
3. After the evening the queue drains; ~2 minutes later (`cooldownPeriod`) the workers scale back to **0**,
   each finishing its current photo before exiting (SIGTERM handling).

k6 prints a summary at the end: `http_req_failed` should stay under 1 % and the
`album_upload_duration` p95 under 5 s (the test's thresholds).
