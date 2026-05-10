#!/bin/sh
set -e

echo "Waiting for database to be ready..."
sleep 3

echo "Running database migrations..."
alembic upgrade head

echo "Starting IBKR Trading Bot..."
exec python -m src.main
