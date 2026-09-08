#!/usr/bin/env bash
# DbOps production lifecycle controller: build | start | stop | restart | status | test
set -euo pipefail
umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
APP_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd)
CONFIG_FILE=${DBOPS_CONFIG_FILE:-"$SCRIPT_DIR/dbops.env"}

if [[ ! -r "$CONFIG_FILE" ]]; then
	echo "Missing production configuration: $CONFIG_FILE" >&2
	exit 2
fi

set -a
# shellcheck disable=SC1090
source "$CONFIG_FILE"
set +a

: "${DBOPS_DATA_DIR:?Set DBOPS_DATA_DIR in dbops.env}"
: "${WEBUI_SECRET_KEY:?Set WEBUI_SECRET_KEY in dbops.env}"
: "${DATABASE_URL:?Set DATABASE_URL in dbops.env}"

DBOPS_HOST=${DBOPS_HOST:-127.0.0.1}
DBOPS_PORT=${DBOPS_PORT:-8080}
RUNTIME_DIR=${DBOPS_RUNTIME_DIR:-"$DBOPS_DATA_DIR/runtime"}
PID_FILE="$RUNTIME_DIR/dbops.pid"
LOG_FILE="$RUNTIME_DIR/dbops.log"
PYTHON_BIN=${DBOPS_PYTHON_BIN:-"$APP_DIR/.venv/bin/python"}
OPEN_WEBUI_BIN=${DBOPS_OPEN_WEBUI_BIN:-"$APP_DIR/.venv/bin/open-webui"}

mkdir -p "$DBOPS_DATA_DIR" "$RUNTIME_DIR"

running_pid() {
	[[ -r "$PID_FILE" ]] || return 1
	local pid
	pid=$(<"$PID_FILE")
	kill -0 "$pid" 2>/dev/null || return 1
	printf '%s' "$pid"
}

build() {
	cd "$APP_DIR"
	npm ci
	npm run build
	[[ -f "$APP_DIR/build/index.html" ]] || { echo 'Frontend build did not produce build/index.html' >&2; exit 1; }
}

start() {
	if running_pid >/dev/null; then
		echo "DbOps already running (PID $(running_pid))"
		return
	fi
	[[ -x "$OPEN_WEBUI_BIN" ]] || { echo "Open WebUI executable not found: $OPEN_WEBUI_BIN" >&2; exit 1; }
	[[ -f "$APP_DIR/build/index.html" ]] || { echo 'Run: dbopsctl.sh build' >&2; exit 1; }
	cd "$APP_DIR/backend"
	nohup "$OPEN_WEBUI_BIN" serve --host "$DBOPS_HOST" --port "$DBOPS_PORT" >>"$LOG_FILE" 2>&1 &
	echo $! >"$PID_FILE"
	sleep 2
	test
	echo "DbOps started (PID $(running_pid))"
}

stop() {
	if ! running_pid >/dev/null; then
		rm -f "$PID_FILE"
		echo 'DbOps is not running'
		return
	fi
	local pid
	pid=$(running_pid)
	kill -TERM "$pid"
	for _ in {1..20}; do
		kill -0 "$pid" 2>/dev/null || { rm -f "$PID_FILE"; echo 'DbOps stopped'; return; }
		sleep 1
	done
	echo "DbOps did not stop gracefully; PID $pid remains" >&2
	exit 1
}

test() {
	"$PYTHON_BIN" - "$DBOPS_HOST" "$DBOPS_PORT" <<'PY'
import sys
from urllib.request import urlopen
host, port = sys.argv[1:]
with urlopen(f'http://{host}:{port}/health', timeout=10) as response:
    body = response.read().decode()
if response.status != 200 or 'true' not in body.lower():
    raise SystemExit(f'Unexpected health response: {body}')
print('DbOps health check passed')
PY
}

status() {
	if running_pid >/dev/null; then
		echo "DbOps running (PID $(running_pid)); http://${DBOPS_HOST}:${DBOPS_PORT}/"
	else
		echo 'DbOps stopped'
	fi
}

case "${1:-}" in
	build) build ;;
	start) start ;;
	stop) stop ;;
	restart) stop; start ;;
	status) status ;;
	test) test ;;
	*) echo "Usage: $0 {build|start|stop|restart|status|test}" >&2; exit 2 ;;
esac
