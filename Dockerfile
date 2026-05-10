FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    dos2unix \
    && rm -rf /var/lib/apt/lists/*

# Copy ALL source first (needed for pip install to find the package)
COPY pyproject.toml ./
COPY src/ src/
COPY alembic/ alembic/
COPY alembic.ini ./
COPY scripts/ scripts/

# Install the package (this makes 'src' importable as a module)
RUN pip install --no-cache-dir -e .

# Copy config (may be overridden by volume mount)
COPY config.yaml ./

# Fix Windows line endings in scripts and make executable
RUN dos2unix scripts/start.sh && chmod +x scripts/start.sh

# Create runtime directories
RUN mkdir -p /app/data /app/logs

# Expose dashboard port
EXPOSE 8080

CMD ["scripts/start.sh"]
