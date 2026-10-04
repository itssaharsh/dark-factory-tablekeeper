#!/usr/bin/env bash
# Start, stop or watch the four seats of one run. Each seat is a process on this machine.
#   usage: run/seats.sh start [real|toy]   run/seats.sh stop   run/seats.sh status   run/seats.sh logs
# BAND_WORKSPACE names the workspace (the folder that holds dark-factory-wearedevs/, band-work/ and
# agent_config.yaml); left out, it is three folders above this file. Logs go to <workspace>/seat-logs/.
# BAND_OPENCODE_SEATS lists the seats that run on the second model family, through OpenCode, comma
# separated (for example BAND_OPENCODE_SEATS=verifier). Their model is BAND_OPENCODE_MODEL. The
# OpenCode server has to be running already: see run/README.md.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="${BAND_WORKSPACE:-$(cd "$HERE/../../.." && pwd)}"
PY="${BAND_PYTHON:-$WORK/dark-factory-wearedevs/.venv/bin/python}"
LOGS="$WORK/seat-logs"
SEATS="coordinator|implementer|falsifier|verifier"
case "${2:-real}" in
  real) R="$WORK/band-work/result";     F="$WORK/band-work/falsifier";     V="$WORK/band-work/verifier" ;;
  toy)  R="$WORK/band-work/toy-result"; F="$WORK/band-work/toy-falsifier"; V="$WORK/band-work/toy-verifier" ;;
  *) echo "unknown kind ${2:-} (real|toy)"; exit 2 ;;
esac

start() {
  [ -f "$WORK/agent_config.yaml" ] || { echo "no agent_config.yaml in $WORK"; exit 1; }
  mkdir -p "$LOGS"
  for pair in "coordinator:$R" "implementer:$R" "falsifier:$F" "verifier:$V"; do
    seat="${pair%%:*}"; folder="${pair#*:}"
    if pgrep -f "run-seat(-opencode)?\.py $seat " >/dev/null; then echo "$seat already runs"; continue; fi
    runner="run-seat.py"; extra=""
    case ",${BAND_OPENCODE_SEATS:-}," in
      *",$seat,"*) runner="run-seat-opencode.py"; extra="--model ${BAND_OPENCODE_MODEL:-featherless/MiniMaxAI/MiniMax-M2.5}" ;;
    esac
    # no API key from the shell: a Claude seat uses the Claude Code sign-in of this machine
    BAND_WORKSPACE="$WORK" nohup env -u ANTHROPIC_API_KEY "$PY" "$HERE/$runner" "$seat" "$folder" "$R/mandates/$seat.md" $extra \
      >> "$LOGS/$seat.log" 2>&1 &
    echo "$seat started (pid $!, runner $runner, folder $folder, log $LOGS/$seat.log)"
  done
}
stop()   { pkill -f "run-seat(-opencode)?\.py ($SEATS) " && echo stopped || echo "nothing ran"; }
status() { pgrep -af "run-seat(-opencode)?\.py ($SEATS) " || echo "no seat runs"; }
logs()   { tail -n 5 "$LOGS"/*.log 2>/dev/null; }

case "${1:-status}" in start|stop|status|logs) "${1:-status}" ;; *) echo "usage: $0 start|stop|status|logs [real|toy]"; exit 2 ;; esac
