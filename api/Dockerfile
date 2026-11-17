FROM python:3.12.16-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Run as a non-root user with a fixed UID (Kubernetes runAsNonRoot checks the numeric UID)
RUN useradd --create-home --uid 10001 appuser
USER 10001

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')"
# --proxy-headers + --forwarded-allow-ips: trust X-Forwarded-* from Traefik / cloudflared, so
#   request URLs show the public https address. --timeout-graceful-shutdown: don't let open
#   SSE streams block a pod from stopping.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*", "--timeout-graceful-shutdown", "10"]
