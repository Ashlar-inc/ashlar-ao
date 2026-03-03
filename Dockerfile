# ── Ashlr AO — Agent Orchestrator ──
# Python 3.12 + tmux + Claude Code CLI

FROM python:3.12-slim AS base

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    tmux \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js (for Claude Code CLI)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI (pinned to major version)
RUN npm install -g @anthropic-ai/claude-code@1

# App directory
WORKDIR /app

# Python dependencies (cached layer — install from pyproject.toml)
COPY pyproject.toml README.md ./
COPY ashlr_ao/ ashlr_ao/
RUN pip install --no-cache-dir .

# Non-root user
RUN useradd --create-home --shell /bin/bash ashlr \
    && mkdir -p /home/ashlr/.ashlr \
    && chown -R ashlr:ashlr /home/ashlr/.ashlr

USER ashlr

# Expose port
EXPOSE 5111

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:5111/api/health || exit 1

# Run server
ENV ASHLR_HOST=0.0.0.0
CMD ["ashlr"]
