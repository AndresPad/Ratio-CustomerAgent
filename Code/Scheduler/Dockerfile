# ────────────────────────────────────────────────────────────
# CustomerAgent investigation scheduler — Container Apps Job
# Single-shot Python image. No web server; the Job runtime invokes
# `python run_investigation.py` and tears the container down.
# ────────────────────────────────────────────────────────────
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install deps first so the layer caches across script edits.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code.
COPY run_investigation.py .

# Non-root for security (Container Apps already restricts root, but
# defense in depth).
RUN useradd --create-home --shell /bin/false scheduler && \
    chown -R scheduler:scheduler /app
USER scheduler

# The Job runtime ignores PORT (no ingress); CMD just runs the script
# and exits. Exit code 0 = success; non-zero = retry per replicaRetryLimit.
CMD ["python", "run_investigation.py"]
