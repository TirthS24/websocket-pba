# LLM service: LangGraph + FastAPI, for EC2 or other hosts
FROM python:3.12-slim

WORKDIR /app

# Copy dependency list first for better layer caching (build from repo root: docker build -f llm/Dockerfile .)
COPY requirements.txt .

# Install uv package manager
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install dependencies using uv pip (installs to system Python)
RUN uv pip install --system --no-cache -r requirements.txt

# Copy application. Build from repo root: docker build -f llm/Dockerfile .
# Then context has llm/ and appdata/ (if at repo root).
COPY src/ ./

# App runs from /app; applib and index are under /app
ENV PYTHONPATH=/app

EXPOSE 7980

# Run FastAPI app via uvicorn
CMD ["uvicorn", "index:app", "--host", "0.0.0.0", "--port", "7980"]
