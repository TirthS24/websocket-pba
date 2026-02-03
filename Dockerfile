#
# Production-style Docker image for Django Channels (ASGI) using Daphne.
# This is suitable for local dev and can be adapted for ECS/EC2.
#

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System deps (kept minimal). Add build tools only if you add native wheels later.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libpq-dev \
  && rm -rf /var/lib/apt/lists/*

# Install Python deps first to maximize Docker layer cache.
# Using uv for faster dependency installation
COPY requirements.txt /app/requirements.txt
COPY pyproject.toml /app/pyproject.toml
RUN pip install --no-cache-dir uv && uv pip install --system -r /app/requirements.txt

# Copy project code.
COPY ws_server/ /app/ws_server/

# Note: .env file should be loaded via docker-compose env_file (see docker-compose.yml)
# We don't copy .env into the image for security reasons - use environment variables instead

# Expose Daphne port.
EXPOSE 8000

# Set PYTHONPATH to include /app so imports like 'ws_server.applib' work correctly.
# When WORKDIR is /app/ws_server, Python needs /app in PYTHONPATH to find ws_server module.
ENV PYTHONPATH=/app

# Set working directory to the outer ws_server folder (where manage.py lives)
# This allows Python to resolve imports like "ws_server.asgi" correctly
WORKDIR /app/ws_server

# Run Daphne with the correct ASGI module path
# ws_server.asgi refers to /app/ws_server/ws_server/asgi.py
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "ws_server.asgi:application"]