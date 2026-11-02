#!/bin/sh
# Start the video engine (server/app.py) as a private service on 127.0.0.1:8700.
# Only the RT-EOS server (rt_eos/, port 3301) talks to it. It loads models, so it
# runs through the model lock with OMP_NUM_THREADS=2 (scripts/with-model-lock.sh).
# Every path it reads points inside this folder.

HERE=$(cd "$(dirname "$0")" && pwd)

export RACKTRACK_HOST=127.0.0.1
export RACKTRACK_PORT=8700
export RACKTRACK_REPO="$HERE/rt_eos"
export DARK_MOBILE="$HERE/vendor/dark_mobile"
export RACKTRACK_SPECS_CLI="$HERE/rt_eos/switch_specs/cli.py"
export RACKTRACK_SPECS_AGENT="$HERE/rt_eos/switch_specs"
export RACKTRACK_TEAM_CAPTURES="$HERE/data/team_captures"
export RACKTRACK_WORKERS=1

cd "$HERE" || exit 1
exec "$HERE/scripts/with-model-lock.sh" "$HERE/_cache/venv/bin/python" "$HERE/server/app.py"
