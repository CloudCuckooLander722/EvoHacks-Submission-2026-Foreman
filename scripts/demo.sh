#!/usr/bin/env bash
# Start the Foreman API (:8000) and the web dev server (:5173) together. Ctrl+C stops both.
# Without keys in .env the site still works in replay mode ("Replay a recorded run").
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
  echo "No .venv yet. Run: python3.12 -m venv .venv && source .venv/bin/activate && pip install -e '.[dev]'" >&2
  exit 1
fi
command -v npm >/dev/null || { echo "npm not found; install Node 20+ (e.g. brew install node)" >&2; exit 1; }
[ -d web/node_modules ] || (cd web && npm install)

source .venv/bin/activate
uvicorn foreman.api.app:app --port 8000 &
API_PID=$!
(cd web && npm run dev -- --port 5173 --strictPort) &
WEB_PID=$!
trap 'kill $API_PID $WEB_PID 2>/dev/null' EXIT INT TERM

sleep 3
echo
echo "  Foreman is up:  http://localhost:5173"
echo "  API:            http://localhost:8000/health"
echo "  Demo file:      data/hero/demo_300.csv  (also downloadable from the page)"
echo
wait
