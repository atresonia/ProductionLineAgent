#!/usr/bin/env bash
# run.sh — start the full Resolve demo stack
# Usage:
#   ./run.sh              # start everything
#   ./run.sh bad_deploy   # start + inject a fault (bad_deploy | slow_db | memory_leak | db_down)
#   ./run.sh multi        # start + inject bad_deploy + slow_db (multi-fault triage demo)
#   ./run.sh stop         # kill dashboard + agent (leaves Docker running)

ROOT="$(cd "$(dirname "$0")" && pwd)"

RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'; CYN='\033[0;36m'; NC='\033[0m'
info() { printf "${CYN}[resolve]${NC} %s\n" "$*"; }
ok()   { printf "${GRN}[resolve]${NC} %s\n" "$*"; }
warn() { printf "${YLW}[resolve]${NC} %s\n" "$*"; }
err()  { printf "${RED}[resolve]${NC} %s\n" "$*"; }

PIDFILE="$ROOT/.run_pids"

# ── Stop mode ────────────────────────────────────────────────────────────────
if [[ "${1:-}" == "stop" ]]; then
  info "Stopping dashboard and agent..."
  if [[ -f "$PIDFILE" ]]; then
    while read -r pid; do
      kill "$pid" 2>/dev/null && echo "  killed $pid" || true
    done < "$PIDFILE"
    rm -f "$PIDFILE"
  fi
  ok "Done. Run 'docker compose down' to also stop Docker services."
  exit 0
fi

# ── Cleanup on Ctrl+C ────────────────────────────────────────────────────────
cleanup() {
  echo ""
  warn "Shutting down dashboard and agent..."
  if [[ -f "$PIDFILE" ]]; then
    while read -r pid; do kill "$pid" 2>/dev/null || true; done < "$PIDFILE"
    rm -f "$PIDFILE"
  fi
}
trap cleanup EXIT INT TERM

rm -f "$PIDFILE"
touch "$PIDFILE"

# ── 1. Docker services ───────────────────────────────────────────────────────
info "Starting Docker services (db → api → frontend)..."
cd "$ROOT"
docker compose up --build -d
ok "Docker services started."

# ── 2. Wait for API ──────────────────────────────────────────────────────────
info "Waiting for api:8000..."
for i in $(seq 1 40); do
  if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
    ok "API is ready."
    break
  fi
  if [[ $i -eq 40 ]]; then
    err "API did not start after 80s. Check logs:"
    err "  docker compose logs api"
    exit 1
  fi
  sleep 2
done

# ── 3. Dashboard ─────────────────────────────────────────────────────────────
# Kill any existing dashboard/agent before starting fresh
pkill -f "dashboard/app.py" 2>/dev/null || true
pkill -f "agent/agent.py"   2>/dev/null || true
sleep 1
info "Installing dashboard deps..."
pip3 install -q flask python-dotenv
info "Starting dashboard on :5050..."
cd "$ROOT/dashboard"
python3 app.py >> "$ROOT/logs/dashboard.log" 2>&1 &
DASH_PID=$!
echo $DASH_PID >> "$PIDFILE"
sleep 2
if ! kill -0 $DASH_PID 2>/dev/null; then
  err "Dashboard failed to start. Last log lines:"
  tail -20 "$ROOT/logs/dashboard.log"
  exit 1
fi
ok "Dashboard running → http://localhost:5050"

# ── 4. Agent ─────────────────────────────────────────────────────────────────
info "Installing agent deps..."
cd "$ROOT/agent"
pip3 install -q -r requirements.txt
info "Starting agent (monitor mode)..."
python3 agent.py >> "$ROOT/logs/agent.log" 2>&1 &
AGENT_PID=$!
echo $AGENT_PID >> "$PIDFILE"
sleep 2
if ! kill -0 $AGENT_PID 2>/dev/null; then
  err "Agent failed to start. Last log lines:"
  tail -20 "$ROOT/logs/agent.log"
  exit 1
fi
ok "Agent running (monitoring)."

# ── 5. Load generator ────────────────────────────────────────────────────────
info "Starting load generator (~2 req/s)..."
cd "$ROOT"
python3 load_gen.py >> "$ROOT/logs/load_gen.log" 2>&1 &
echo $! >> "$PIDFILE"
ok "Load generator running."

# ── 6. Optional fault injection ──────────────────────────────────────────────
FAULT="${1:-}"
if [[ "$FAULT" == "multi" ]]; then
  sleep 2
  warn "Injecting multi-fault: bad_deploy + slow_db"
  cd "$ROOT" && python3 chaos.py bad_deploy slow_db
elif [[ -n "$FAULT" && "$FAULT" != "stop" ]]; then
  sleep 2
  warn "Injecting fault: $FAULT"
  cd "$ROOT" && python3 chaos.py "$FAULT"
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
printf "${GRN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
printf "${GRN} Resolve stack is running — press Ctrl+C to stop${NC}\n"
printf "${GRN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
printf "  Dashboard  → ${CYN}http://localhost:5050${NC}\n"
printf "  API        → ${CYN}http://localhost:8000${NC}\n"
printf "  Frontend   → ${CYN}http://localhost:3000${NC}\n"
echo ""
printf "  Inject fault:   ${YLW}python3 chaos.py bad_deploy${NC}\n"
printf "  Multi-fault:    ${YLW}python3 chaos.py bad_deploy slow_db${NC}\n"
printf "  Clear faults:   ${YLW}python3 chaos.py none${NC}\n"
printf "  Stop all:       ${YLW}./run.sh stop${NC}\n"
echo ""
printf "  Agent log:      ${YLW}tail -f logs/agent.log${NC}\n"
printf "  Dashboard log:  ${YLW}tail -f logs/dashboard.log${NC}\n"
printf "${GRN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"

# Keep alive — Ctrl+C triggers cleanup
wait
