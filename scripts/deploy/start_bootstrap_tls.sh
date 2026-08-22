#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "start_bootstrap_tls.sh must run as root" >&2
  exit 1
fi
if [[ $# -ne 1 || ! $1 =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$ ]]; then
  echo "usage: start_bootstrap_tls.sh production.example.com" >&2
  exit 2
fi

PUBLIC_HOST=$1
BOOTSTRAP_DIR=/opt/storecipe/bootstrap
install -d -m 0755 -o root -g root "$BOOTSTRAP_DIR"
cat > "$BOOTSTRAP_DIR/Caddyfile" <<EOF
$PUBLIC_HOST {
  root * /srv
  file_server
}
EOF
chmod 0644 "$BOOTSTRAP_DIR/Caddyfile"

docker volume create storecipe-production_caddy-data >/dev/null
docker rm -f storecipe-bootstrap >/dev/null 2>&1 || true
docker run -d --name storecipe-bootstrap --restart unless-stopped \
  -p 80:80 -p 443:443 -p 443:443/udp \
  -v "$BOOTSTRAP_DIR/index.html:/srv/index.html:ro" \
  -v "$BOOTSTRAP_DIR/Caddyfile:/etc/caddy/Caddyfile:ro" \
  -v storecipe-production_caddy-data:/data \
  caddy:2.11.4-alpine

echo "Bootstrap TLS requested for $PUBLIC_HOST. Certificate issuance is asynchronous."
echo "Wait and verify the public HTTPS page before creating Auth0 production resources."
