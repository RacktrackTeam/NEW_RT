#!/bin/sh
# Bring RackTrack up on this Mac: the sign-in service, the mocks and the engine (./start.sh), the front door on 8710 and the
# tunnel to it, each only when it is not already answering. Run by the login item ~/Library/LaunchAgents/ai.racktrack.boot.plist
# at every login (owner, 10 Nov 2026: the services did not survive a reboot), or by hand:
#
#     tools/boot.sh            everything
#     tools/boot.sh tunnel     the tunnel only
#
# The volume may mount a little after login: the script waits up to three minutes for it. Logs: data/logs/boot.log.
# Turn the login item off with:  launchctl bootout gui/$(id -u)/ai.racktrack.boot && rm ~/Library/LaunchAgents/ai.racktrack.boot.plist
HERE=$(cd "$(dirname "$0")/.." 2>/dev/null && pwd); ROOT=${HERE:-/Volumes/Racktrack/racktrack-app-integration}
export PATH="$HOME/.nvm/versions/node/v20.20.2/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export OMP_NUM_THREADS=${OMP_NUM_THREADS:-2}
NGROK=${NGROK:-$HOME/bin/ngrok}; TUNNEL_URL=${RACKTRACK_TUNNEL_URL:-pacifism-appraiser-wiring.ngrok-free.dev}
n=0; while [ ! -x "$ROOT/start.sh" ] && [ "$n" -lt 36 ]; do sleep 5; n=$((n + 1)); done
cd "$ROOT" || { echo "$(date '+%F %T') the RackTrack folder is not there: $ROOT"; exit 1; }
mkdir -p data/logs data/pids
up() { curl -s -o /dev/null --max-time 2 "$1"; }
echo "$(date '+%F %T') boot ${1:-all}"
if [ "${1:-all}" = "all" ]; then
  ./start.sh
  if up http://127.0.0.1:8710/api/auth-service; then echo "front door: already running"; else
    nohup node tools/front.js > data/logs/front.log 2>&1 &
    echo $! > data/pids/front.pid; sleep 1
    up http://127.0.0.1:8710/api/auth-service && echo "front door: running (8710, pid $(cat data/pids/front.pid))" || echo "front door: did not answer; see data/logs/front.log"
  fi
fi
if up http://127.0.0.1:4040/api/tunnels; then echo "tunnel: already running"; else
  if [ -x "$NGROK" ]; then
    nohup "$NGROK" http --url="$TUNNEL_URL" 8710 > data/logs/ngrok.log 2>&1 &
    echo $! > data/pids/ngrok.pid; sleep 4
    if ! up http://127.0.0.1:4040/api/tunnels; then                      # the account may not hold that address: a plain tunnel then
      nohup "$NGROK" http 8710 > data/logs/ngrok.log 2>&1 &
      echo $! > data/pids/ngrok.pid; sleep 4
    fi
    up http://127.0.0.1:4040/api/tunnels && echo "tunnel: $(curl -s --max-time 2 http://127.0.0.1:4040/api/tunnels | sed 's/.*"public_url":"\([^"]*\)".*/\1/')/app/" || echo "tunnel: did not start; see data/logs/ngrok.log"
  else
    echo "tunnel: ngrok is not at $NGROK"
  fi
fi
