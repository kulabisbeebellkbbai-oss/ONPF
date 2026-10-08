#!/bin/sh
# Later launches never install packages. Linux runtime verification is documented separately.
set -eu
repository=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
python_path="$repository/.venv/bin/python"
instance_path=${1:-"$repository/instance"}
port=${2:-8765}
database_path=${3:-"$instance_path/onpf.sqlite3"}
if [ ! -x "$python_path" ]; then
    echo 'Complete README first-time setup. This launcher does not install dependencies.' >&2
    exit 1
fi
if [ ! -f "$instance_path/.instance-id" ] || [ ! -f "$database_path" ]; then
    echo 'Complete README first-time setup: initialize storage and create an account explicitly.' >&2
    exit 1
fi
check_ready() {
    "$python_path" - "$instance_path" "$port" "$database_path" <<'PY'
import json, socket, sys, urllib.request
from pathlib import Path
from onpf.config import readiness_identity
instance, port = Path(sys.argv[1]).resolve(), int(sys.argv[2])
if not 1 <= port <= 65535:
    raise SystemExit('Choose a port from 1 to 65535.')
try:
    with socket.create_connection(('127.0.0.1', port), timeout=1):
        pass
except OSError:
    raise SystemExit(1)
try:
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=2) as response:
        status = json.load(response)
    if status == {'application': 'onpf', 'version': '0.3.0', 'instance_id': readiness_identity(instance, Path(sys.argv[3]))}:
        raise SystemExit(0)
except (OSError, ValueError):
    pass
raise SystemExit(2)
PY
}
state=0
check_ready || state=$?
if [ "$state" -eq 2 ]; then
    echo "Port conflict at http://127.0.0.1:$port; no service was stopped." >&2
    exit 1
elif [ "$state" -eq 0 ]; then
    echo "Reusing ONPF at http://127.0.0.1:$port for the selected instance."
else
    nohup "$python_path" -m onpf.cli --instance "$instance_path" serve --database "$database_path" --host 127.0.0.1 --port "$port" >"$instance_path/server.stdout.log" 2>"$instance_path/server.stderr.log" &
    started_pid=$!
    ready=0
    attempts=0
    while [ "$attempts" -lt 100 ]; do
        if check_ready; then ready=1; break; fi
        if ! kill -0 "$started_pid" 2>/dev/null; then break; fi
        attempts=$((attempts + 1))
        sleep 0.2
    done
    if [ "$ready" -ne 1 ]; then
        kill "$started_pid" 2>/dev/null || true
        echo 'ONPF did not become ready; read the instance server.stderr.log.' >&2
        exit 1
    fi
    printf '%s\n' "$started_pid" >"$instance_path/.server.pid"
    echo "Started ONPF at http://127.0.0.1:$port (process $started_pid)."
fi
"$python_path" -m webbrowser "http://127.0.0.1:$port/login" >/dev/null 2>&1 || true
