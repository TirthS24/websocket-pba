#
# Production-style Docker image for Django Channels (ASGI) using Daphne.
# This is suitable for local dev and can be adapted for ECS/EC2.
#

FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System deps (kept minimal). Add build tools only if you add native wheels later.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
  && rm -rf /var/lib/apt/lists/*

# Install Python deps first to maximize Docker layer cache.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -U pip && pip install --no-cache-dir -r /app/requirements.txt

# Copy project code.
COPY ws_server/ /app/ws_server/

# Expose Daphne port.
EXPOSE 8000

# Run Daphne (NOT Django runserver).
# Note: working directory must contain `manage.py` for Django path expectations.
WORKDIR /app/ws_server

CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "ws_server.asgi:application"]

