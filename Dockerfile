#
# Production image: ws_server (Django/Daphne on 8000) + LLM (FastAPI/uvicorn on 7980).
# Used for ECS Fargate; both services run in the same container.
#

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System deps (minimal)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
  && rm -rf /var/lib/apt/lists/*

# Install ws_server (Django/Channels/Daphne) dependencies
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -U pip && pip install --no-cache-dir -r /app/requirements.txt

# Install LLM service dependencies (FastAPI, LangGraph, uvicorn, etc.)
COPY llm/requirements.txt /app/llm_requirements.txt
RUN pip install --no-cache-dir -r /app/llm_requirements.txt

# Copy ws_server and LLM app code
COPY ws_server/ /app/ws_server/
COPY llm/src/ /app/llm/
ENV PYTHONPATH=/app/llm

# Expose both ports
EXPOSE 8000 7980

# Entrypoint runs both servers in the same container
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
