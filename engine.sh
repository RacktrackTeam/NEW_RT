#!/bin/sh
# Start the video engine (server/app.py) as a private service on 127.0.0.1:8700 (RACKTRACK_PORT to change it).
# Start ./auth.sh first: every /api route except health needs its sign-in.
# Only the app's screens, signed in through the sign-in service (server/auth, 3302), talk to it. It loads models, so it
# takes the machine-wide model lock only around a model run, with OMP_NUM_THREADS=2.
# Every path it reads points inside this folder.

HERE=$(cd "$(dirname "$0")" && pwd)

export RACKTRACK_HOST=127.0.0.1
export RACKTRACK_PORT=${RACKTRACK_PORT:-8700}
# racktrack-app sign-in: the engine checks the session the sign-in service (./auth.sh) signs, with its JWT_SECRET.
export RT_AUTH_ENV="${RT_AUTH_ENV:-$HERE/data/auth/.env}"
export RACKTRACK_REPO="$HERE/engine/readers"          # RT-EOS's readers and its Models link
export RACKTRACK_SPECS_CLI="$HERE/engine/readers/switch_specs/cli.py"
export RACKTRACK_SPECS_AGENT="$HERE/engine/readers/switch_specs"
export RACKTRACK_TEAM_CAPTURES="$HERE/data/team_captures"
export RACKTRACK_WORKERS=1
# No call leaves this machine (hard rule 2): the port agents' vendor-site and image search is off, and the specifications
# lookup reads only the local knowledge base. RACKTRACK_ONLINE_LOOKUPS=1 would let an organization whose online lookups
# setting is on reach the makers' pages; leave it off here.
export SWITCH_MAPPER_WEB_SEARCH=0
export RACKTRACK_ONLINE_LOOKUPS=0
# No model load reaches the internet either: every pretrained weight is read from models/ inside this folder
# (src/local_models.py names them), and the Hugging Face, torch hub and Ultralytics lookups are off.
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 YOLO_OFFLINE=1
export HF_HOME="$HERE/models/huggingface" TORCH_HOME="$HERE/models/torch" EASYOCR_MODULE_PATH="$HERE/models/easyocr"
export YOLO_CONFIG_DIR="$HERE/_cache/ultralytics"; mkdir -p "$YOLO_CONFIG_DIR"

cd "$HERE" || exit 1
export OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2
# No lock wrapper: the server takes the machine-wide model lock itself, only around a model run (server/app.py ModelLock),
# so an idle server never holds it.
# The engine's own environment (tools/make_engine_env.py: racktrack-app's tested packages, numpy 2); the RT-EOS readers it
# calls run in RT-EOS's environment, _cache/venv, as child processes (src/rt_features.py RTEOS_PYTHON).
exec "$HERE/_cache/venv-engine/bin/python" "$HERE/server/app.py"
