FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy project files
COPY pyproject.toml ./
COPY src/ ./src/

# Install with orchestrator extras
RUN uv pip install --system ".[orchestrator]"

EXPOSE 8420

CMD ["legatus-orchestrator"]
