FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (add bash + curl for reliability)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    bash \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

# Copy application source
COPY src/ src/
COPY alembic/ alembic/
COPY alembic.ini ./
COPY config.yaml ./
COPY scripts/ scripts/

# Ensure script is executable
RUN chmod +x scripts/start.sh

# Create runtime directories
RUN mkdir -p /app/data /app/logs

# Expose dashboard port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# IMPORTANT FIX:
# Use bash explicitly to avoid /bin/sh (dash) weirdness
CMD ["bash", "scripts/start.sh"]