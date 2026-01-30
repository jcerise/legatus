FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl && rm -rf /var/lib/apt/lists/*

# Install Node.js (required for Claude Code CLI)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy project files
COPY pyproject.toml ./
COPY src/ ./src/

# Install with agent extras
RUN uv pip install --system ".[agent]"

# Working directory for the user's project
RUN mkdir -p /workspace
WORKDIR /workspace

CMD ["legatus-agent"]
