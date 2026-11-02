#!/bin/bash
# Pushes racktrack-app (main, tag before-rt-eos) and the integration branch to github.com/RacktrackTeam/racktrack_app.
# Run it in Terminal:  bash /Volumes/Racktrack/racktrack-app/push-to-github.sh
# Git asks once for the GitHub username and a personal access token with repo scope, then keeps it in the keychain.
set -e
git -C /Volumes/Racktrack/racktrack-app push -u origin main --tags
git -C /Volumes/Racktrack/racktrack-app-integration push -u origin rt-eos-integration
echo "Pushed: main, tag before-rt-eos, branch rt-eos-integration"
