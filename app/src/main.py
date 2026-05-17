import logging
import os
import sys
import json
import time
import random
from datetime import datetime

import fastapi
import fastapi.responses
from prometheus_client import Counter, Gauge, Histogram, generate_latest, REGISTRY
import uvicorn

# ----- Logging Setup (JSON structured) -----
class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "path": getattr(record, "path", ""),
            "method": getattr(record, "method", ""),
            "status": getattr(record, "status", 0),
            "duration_ms": getattr(record, "duration_ms", 0),
        }
        return json.dumps(log_record)

logger = logging.getLogger("observability-app")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JsonFormatter())
logger.handlers = [handler]

# ----- Prometheus Metrics -----
REQUEST_COUNT = Counter('http_requests_total', 'Total HTTP requests', ['method', 'endpoint', 'status'])
REQUEST_LATENCY = Histogram('http_request_duration_seconds', 'Request latency in seconds', ['method', 'endpoint'])
IN_FLIGHT = Gauge('http_requests_in_flight', 'Requests currently in flight')

# ----- FastAPI App -----
app = fastapi.FastAPI()

@app.middleware("http")
async def observability_middleware(request: fastapi.Request, call_next):
    """Force text/plain content type for /metrics endpoint."""
    response = await call_next(request)
    if request.url.path == "/metrics":
        response.headers["Content-Type"] = "text/plain; charset=utf-8"
    return response

@app.middleware("http")
async def observability_middleware(request: fastapi.Request, call_next):
    if request.url.path == "metrics":
        return await call_next(request)
    
    # Increment in-flight counter
    IN_FLIGHT.inc()
    start_time = time.time()

    response = await call_next(request)

    # Compute metrics
    duration = time.time() - start_time
    status_code = response.status_code
    method = request.method
    endpoint = request.url.path

    REQUEST_COUNT.labels(method=method, endpoint=endpoint, status=status_code).inc()
    REQUEST_LATENCY.labels(method=method, endpoint=endpoint).observe(duration)
    IN_FLIGHT.dec()

    # Attach extra fields to the log record
    logger.info(
        f"{method} {endpoint} {status_code}",
        extra={
            "path": endpoint,
            "method": method,
            "status": status_code,
            "duration_ms": round(duration * 1000, 2)
        }
    )
    return response

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/hello")
def hello(name: str = "World"):
    # Simulate a bit of processing
    time.sleep(random.uniform(0.01, 0.2))
    return {"message": f"Hello, {name}!"}

@app.get("/metrics")
def metrics():
    return fastapi.responses.Response(content=generate_latest(REGISTRY), media_type="text/plain; charset=utf-8")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
