#!/bin/bash
# Start the RackTrack server.   ./server/run.sh            http on port 8600
#                               ./server/run.sh --https    https with server/cert.pem + server/key.pem (make them with ./server/make_cert.sh)
cd "$(dirname "$0")/.." || exit 1
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-2}"
if [ "$1" = "--https" ]; then export RACKTRACK_CERT=server/cert.pem RACKTRACK_KEY=server/key.pem; fi
if command -v caffeinate >/dev/null; then exec caffeinate -i python3 server/app.py; else exec python3 server/app.py; fi   # caffeinate: the Mac must not sleep while a run is going
