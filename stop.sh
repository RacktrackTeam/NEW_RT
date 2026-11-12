#!/bin/sh
# Stop what ./start.sh started, by the process ids it wrote in data/pids (the engine, the mocks, the sign-in service).
# A service start.sh found already running (and did not start) has no pid file and is left alone.

HERE=$(cd "$(dirname "$0")" && pwd)
PIDS="${RACKTRACK_DATA:-$HERE/data}/pids"
for name in engine mocks sign-in; do
  f="$PIDS/$name.pid"
  [ -f "$f" ] || { echo "$name: not started by start.sh"; continue; }
  pid=$(cat "$f")
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "$name: already stopped"; rm -f "$f"; continue
  fi
  case "$(ps -p "$pid" -o command= 2>/dev/null)" in
    *"$HERE"*|*node*|*python*|*sources.sh*) ;;
    *) echo "$name: pid $pid is not ours any more, left alone"; rm -f "$f"; continue ;;
  esac
  pkill -TERM -P "$pid" 2>/dev/null          # the mocks script's two node processes
  kill -TERM "$pid" 2>/dev/null
  n=0; while kill -0 "$pid" 2>/dev/null && [ "$n" -lt 10 ]; do sleep 1; n=$((n + 1)); done
  kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" 2>/dev/null
  rm -f "$f"
  echo "$name: stopped (pid $pid)"
done
