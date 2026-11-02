#!/bin/sh
# Start the video engine (server/app.py) as a private service on 127.0.0.1:8700 (RACKTRACK_PORT to change it).
# Start ./auth.sh first: every /api route except health needs its sign-in.
# Only the RT-EOS server (rt_eos/, the sign-in service on 3302) talks to it. It loads models, so it
# takes the machine-wide model lock only around a model run, with OMP_NUM_THREADS=2.
# Every path it reads points inside this folder.

HERE=$(cd "$(dirname "$0")" && pwd)

export RACKTRACK_HOST=127.0.0.1
export RACKTRACK_PORT=${RACKTRACK_PORT:-8700}
# racktrack-app sign-in: the engine checks the session the sign-in service (./auth.sh) signs, with its JWT_SECRET.
export RT_AUTH_ENV="$HERE/data/auth/.env"
export RACKTRACK_REPO="$HERE/rt_eos"
export DARK_MOBILE="$HERE/vendor/dark_mobile"
export RACKTRACK_SPECS_CLI="$HERE/rt_eos/switch_specs/cli.py"
export RACKTRACK_SPECS_AGENT="$HERE/rt_eos/switch_specs"
export RACKTRACK_TEAM_CAPTURES="$HERE/data/team_captures"
export RACKTRACK_WORKERS=1

cd "$HERE" || exit 1
export OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2
# No lock wrapper: the server takes the machine-wide model lock itself, only around a model run (server/app.py ModelLock),
# so an idle server never holds it.
exec "$HERE/_cache/venv/bin/python" "$HERE/server/app.py"
