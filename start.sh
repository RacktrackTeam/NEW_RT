#!/bin/sh
# Start RackTrack: the sign-in service, the local database mocks (only when no real login is saved) and the engine, in
# that order, each checked before the next starts. One line per service. Stop them with ./stop.sh.
#
#   ./start.sh            then open http://127.0.0.1:8700/app/
#
# Ports: sign-in service 3302, NetBox mock 3303, ServiceNow mock 3304, engine 8700 (RACKTRACK_PORT for another).
# A service already answering on its port is left as it is (it is not started twice, and stop.sh does not stop it).
# Logs: data/logs/<service>.log. Process ids: data/pids/<service>.pid.

HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE" || exit 1
LOGS="$HERE/data/logs"; PIDS="$HERE/data/pids"
mkdir -p "$LOGS" "$PIDS"
ENGINE_PORT=${RACKTRACK_PORT:-8700}

up() {     # up <url>: true when the address answers
  curl -s -o /dev/null --max-time 2 "$1"
}

wait_up() {     # wait_up <url> <seconds>
  n=0
  while [ "$n" -lt "$2" ]; do
    up "$1" && return 0
    sleep 1; n=$((n + 1))
  done
  return 1
}

start() {     # start <name> <health url> <seconds> <command...>
  name=$1; url=$2; secs=$3; shift 3
  if up "$url"; then
    echo "$name: already running ($url), left as it is"
    return 0
  fi
  nohup "$@" > "$LOGS/$name.log" 2>&1 &
  echo $! > "$PIDS/$name.pid"
  if wait_up "$url" "$secs"; then
    echo "$name: running ($url, pid $(cat "$PIDS/$name.pid"))"
  else
    echo "$name: did not answer within $secs s; see data/logs/$name.log"
    exit 1
  fi
}

# 1 the sign-in service (it makes data/auth/.env, builds the /org/ pages and seeds the accounts on its first start)
start sign-in "http://127.0.0.1:3302/api/health" 120 "$HERE/auth.sh"

# 2 the local NetBox and ServiceNow, unless an organization saved a login to a real one (Profile > Connections)
REAL=$("$HERE/_cache/venv-engine/bin/python" - "$HERE/data" <<'PY'
import pathlib, sqlite3, sys, urllib.parse
n = 0
for db in [pathlib.Path(sys.argv[1]) / "sources.db", *pathlib.Path(sys.argv[1]).glob("sources_by_org/*/sources.db")]:
    if not db.exists():
        continue
    try:
        c = sqlite3.connect(db)
        rows = c.execute("SELECT address FROM connection_profiles").fetchall() if c.execute(
            "SELECT 1 FROM sqlite_master WHERE name='connection_profiles'").fetchone() else []
        c.close()
    except sqlite3.Error:
        rows = []
    n += sum(1 for (a,) in rows if (urllib.parse.urlparse(a).hostname or "") not in ("127.0.0.1", "localhost", ""))
print(n)
PY
)
if [ "${REAL:-0}" -gt 0 ]; then
  echo "mocks: not started ($REAL real login(s) saved)"
else
  start mocks "http://127.0.0.1:3304/api/now/table/cmdb_ci_rack?sysparm_limit=1" 20 "$HERE/sources.sh"
fi

# 3 the engine (it takes the model lock only around a model run)
start engine "http://127.0.0.1:$ENGINE_PORT/api/health" 180 "$HERE/engine.sh"

echo "open http://127.0.0.1:$ENGINE_PORT/app/"
