#!/bin/bash
PORT=${PORT:-5000}
echo "Starting on port $PORT"
exec gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 300
