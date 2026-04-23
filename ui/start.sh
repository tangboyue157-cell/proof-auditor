#!/usr/bin/env bash
# ────────────────────────────────────────────────────────
#  Proof Auditor UI — Launcher
#  Usage: bash ui/start.sh [OPTIONS]
#
#  Options:
#    --port PORT     Server port (default: 3000)
#    --dev           Dev mode: tsx watch + vite dev server
#    --build         Build client only, don't start server
#    --open          Open browser after starting
#    -h, --help      Show help
# ────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
UI_DIR="$SCRIPT_DIR"

PORT=3000
DEV_MODE=false
BUILD_ONLY=false
OPEN_BROWSER=false

# Parse arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)   PORT="$2"; shift 2 ;;
    --dev)    DEV_MODE=true; shift ;;
    --build)  BUILD_ONLY=true; shift ;;
    --open)   OPEN_BROWSER=true; shift ;;
    -h|--help)
      echo "Usage: bash ui/start.sh [--port PORT] [--dev] [--build] [--open]"
      echo ""
      echo "  --port PORT   Server port (default: 3000)"
      echo "  --dev         Dev mode with hot reload"
      echo "  --build       Build client only"
      echo "  --open        Open browser after starting"
      exit 0
      ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# ── Check dependencies ──
check_cmd() {
  if ! command -v "$1" &>/dev/null; then
    echo "❌ $1 not found. Please install it first."
    exit 1
  fi
}

check_cmd node
check_cmd npm

NODE_VER=$(node -v | sed 's/v//' | cut -d. -f1)
if [[ "$NODE_VER" -lt 18 ]]; then
  echo "❌ Node.js 18+ required (found: $(node -v))"
  exit 1
fi

echo ""
echo "  🔬 Proof Auditor UI"
echo "  ───────────────────"
echo "  Project:  $PROJECT_DIR"
echo "  UI Dir:   $UI_DIR"
echo "  Port:     $PORT"
echo "  Mode:     $(if $DEV_MODE; then echo 'Development'; else echo 'Production'; fi)"
echo ""

# ── Install dependencies ──
if [[ ! -d "$UI_DIR/server/node_modules" ]] || [[ ! -d "$UI_DIR/client/node_modules" ]]; then
  echo "📦 Installing dependencies..."
  (cd "$UI_DIR/server" && npm install --no-fund --no-audit)
  (cd "$UI_DIR/client" && npm install --no-fund --no-audit)
  echo "  ✅ Dependencies installed"
  echo ""
fi

# ── Build client (production mode) ──
if ! $DEV_MODE; then
  if [[ ! -d "$UI_DIR/client/dist" ]] || $BUILD_ONLY; then
    echo "🔨 Building client..."
    (cd "$UI_DIR/client" && npx vite build)
    echo "  ✅ Client built"
    echo ""
  fi
fi

if $BUILD_ONLY; then
  echo "✅ Build complete. Run 'bash ui/start.sh' to start the server."
  exit 0
fi

# ── Start server ──
if $DEV_MODE; then
  echo "🚀 Starting in development mode..."
  echo "  → Server:  http://localhost:$PORT (tsx watch)"
  echo "  → Client:  http://localhost:5173 (vite dev)"
  echo ""

  # Start server in background
  (cd "$UI_DIR/server" && npx tsx watch src/index.ts -- --project "$PROJECT_DIR" --port "$PORT") &
  SERVER_PID=$!

  # Start vite dev server
  (cd "$UI_DIR/client" && npx vite --port 5173) &
  CLIENT_PID=$!

  # Open browser
  if $OPEN_BROWSER; then
    sleep 2
    if command -v open &>/dev/null; then
      open "http://localhost:5173"
    elif command -v xdg-open &>/dev/null; then
      xdg-open "http://localhost:5173"
    fi
  fi

  # Cleanup on exit
  trap "kill $SERVER_PID $CLIENT_PID 2>/dev/null; exit" INT TERM
  wait
else
  echo "🚀 Starting server on http://localhost:$PORT"
  echo ""

  if $OPEN_BROWSER; then
    (sleep 1 && {
      if command -v open &>/dev/null; then
        open "http://localhost:$PORT"
      elif command -v xdg-open &>/dev/null; then
        xdg-open "http://localhost:$PORT"
      fi
    }) &
  fi

  cd "$UI_DIR/server"
  exec npx tsx src/index.ts -- --project "$PROJECT_DIR" --port "$PORT"
fi
