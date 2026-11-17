FROM python:3.12.16-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY worker.py storage.py ./

RUN useradd --create-home --uid 10001 appuser
USER 10001

# Prometheus metrics
EXPOSE 9100
# -u = unbuffered, so logs show up immediately in `docker compose logs` / `kubectl logs`
CMD ["python", "-u", "worker.py"]
