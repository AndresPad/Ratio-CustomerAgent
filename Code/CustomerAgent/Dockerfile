FROM python:3.11-slim

WORKDIR /app

# ── Build context is CustomerAgent/ ─────────────────────────────────────────
# Build:  cd CustomerAgent && docker build -t customer-agent .
# ACR:    az acr build -t customer-agent .

# Install dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code into /app/src/
COPY src/ ./src/

# .env values should be injected via container runtime env vars
# (App Service configuration, ACA secrets, etc.)

ENV PYTHONPATH=/app/src

# ALLOWED_ORIGINS controls CORS; comma-separated list.
# Override at deploy time for production origins.
ENV ALLOWED_ORIGINS="*"

EXPOSE 8503

CMD ["python", "-m", "uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8503"]
