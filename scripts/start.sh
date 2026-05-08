#!/bin/sh
set -e

echo "Running database migrations..."
alembic upgrade head

echo "Starting IBKR Trading Bot..."
exec python -m src.main
