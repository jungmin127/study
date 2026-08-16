#!/usr/bin/env bash
set -euo pipefail

# Tailscale HTTPS 인증서는 90일마다 만료된다(Let's Encrypt 기반). cron.monthly로
# 매달 재발급해 만료 전에 항상 갱신되게 한다(deploy/nginx/README 참고).

HOSTNAME="upbit-server.tailb1c1e9.ts.net"
CERT_DIR="/etc/nginx/tailscale-certs"

tailscale cert --cert-file="$CERT_DIR/upbit-server.crt" --key-file="$CERT_DIR/upbit-server.key" "$HOSTNAME"
systemctl reload nginx
