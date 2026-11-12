#!/bin/bash
# Pushes this folder's integration branch to its own remote (origin). It touches no other folder: the live
# racktrack-app is never pushed from here. The owner runs it in Terminal:
#   bash scripts/push-to-github.sh
# Git asks once for the GitHub username and a personal access token with repo scope, then keeps it in the keychain.
set -e
HERE=$(cd "$(dirname "$0")/.." && pwd)
git -C "$HERE" push -u origin rt-eos-integration
echo "Pushed: branch rt-eos-integration of $HERE"
