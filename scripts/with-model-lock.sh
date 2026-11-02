#!/bin/sh
# Run one command while holding the machine-wide model lock.
#
#   scripts/with-model-lock.sh <command> [args...]
#
# This Mac overheats when two things load models at once, so anything that
# loads a model (the server with its worker pool, a pipeline test, a one-off
# read) runs through this script and waits its turn. macOS has no flock, so
# the lock is a directory: mkdir either makes it or fails, atomically, and
# the directory holds the pid of the holder so a lock left by a dead process
# is cleared by the next caller.
#
# The command runs with OMP_NUM_THREADS=2 (and the matching MKL and OpenBLAS
# settings), which keeps one model run to two cores.
#
# Settings:
#   RT_MODEL_LOCK_DIR   the lock directory (default /tmp/one_racktrack_model.lock,
#                       one per machine; every checkout and worktree shares it)
#   RT_MODEL_LOCK_WAIT  seconds to wait before giving up (default 0: wait forever)

set -u

LOCK_DIR="${RT_MODEL_LOCK_DIR:-/tmp/one_racktrack_model.lock}"
MAX_WAIT="${RT_MODEL_LOCK_WAIT:-0}"

if [ "$#" -eq 0 ]; then
  echo "usage: $0 <command> [args...]" >&2
  exit 64
fi

waited=0
while ! mkdir "$LOCK_DIR" 2>/dev/null; do
  holder=$(cat "$LOCK_DIR/pid" 2>/dev/null || echo "")
  if [ -n "$holder" ] && ! kill -0 "$holder" 2>/dev/null; then
    # The holder is gone. Take the lock over.
    rm -rf "$LOCK_DIR"
    continue
  fi
  if [ "$MAX_WAIT" -gt 0 ] && [ "$waited" -ge "$MAX_WAIT" ]; then
    echo "with-model-lock: gave up after ${waited}s; held by pid ${holder:-unknown}" >&2
    exit 75
  fi
  if [ $((waited % 30)) -eq 0 ]; then
    echo "with-model-lock: waiting for the model lock (held by pid ${holder:-unknown})" >&2
  fi
  sleep 2
  waited=$((waited + 2))
done

echo "$$" > "$LOCK_DIR/pid"
cleanup() { rm -rf "$LOCK_DIR"; }
trap cleanup EXIT INT TERM HUP

export OMP_NUM_THREADS=2
export MKL_NUM_THREADS=2
export OPENBLAS_NUM_THREADS=2

"$@"
status=$?
exit $status
