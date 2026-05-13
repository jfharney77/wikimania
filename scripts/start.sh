#!/usr/bin/env bash
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "Starting PostgreSQL..."
if pg_isready -q 2>/dev/null; then
  echo "  PostgreSQL already running."
else
  sudo service postgresql start
fi

echo "Starting Wikimania backend..."
cd "$ROOT/backend"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt -q
fi
.venv/bin/uvicorn main:app --reload --port 8001 &
echo $! > /tmp/wikimania_backend.pid

echo "Starting Wikimania frontend..."
cd "$ROOT/frontend"
if [ ! -d node_modules ]; then npm install -q; fi
npm run dev &
echo $! > /tmp/wikimania_frontend.pid

echo "Backend: http://localhost:8001"
echo "Frontend: http://localhost:5173"
