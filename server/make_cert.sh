#!/bin/bash
# A self-signed certificate for the LAN, so phones can use the camera (browsers allow it on https only).
# Phones must trust it once: open https://<this-mac-ip>:8600/ and accept, or install server/cert.pem as a profile.
cd "$(dirname "$0")" || exit 1
IP=$(ipconfig getifaddr en0 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')
openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 825 -subj "/CN=racktrack" \
  -addext "subjectAltName=IP:${IP:-127.0.0.1},DNS:localhost,DNS:$(hostname)" 2>/dev/null && chmod 600 key.pem && echo "cert.pem and key.pem made for ${IP:-127.0.0.1}"
