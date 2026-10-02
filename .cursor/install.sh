#!/usr/bin/env bash
# Idempotent Cloud Agent bootstrap for the Storecipe monorepo.
# Installs system tooling (Docker + fuse-overlayfs, uv), configures the nested
# Docker daemon, and refreshes Python and web dependencies. It does not start
# long-running services; per-boot startup lives in start.sh / terminals.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

log() { printf '\n=== %s ===\n' "$*"; }

# --- System packages: Docker Engine + Compose plugin ------------------------
if ! command -v docker >/dev/null 2>&1; then
  log "Installing Docker Engine"
  curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
  sudo sh /tmp/get-docker.sh
fi

# --- fuse-overlayfs: required for nested Docker image builds -----------------
if ! command -v fuse-overlayfs >/dev/null 2>&1; then
  log "Installing fuse-overlayfs"
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
    fuse3 fuse-overlayfs || true
  # Some containers fail the post-install conffile prompt; finish non-interactively.
  sudo DEBIAN_FRONTEND=noninteractive dpkg --configure -a --force-confold
fi

# --- Docker daemon config: nested VM needs fuse-overlayfs + legacy iptables --
log "Configuring Docker daemon for the nested VM"
sudo mkdir -p /etc/docker
printf '%s\n' '{
  "storage-driver": "fuse-overlayfs",
  "features": { "containerd-snapshotter": false }
}' | sudo tee /etc/docker/daemon.json >/dev/null

# Docker programs its bridge rules where the FORWARD policy lives; the nested
# VM enforces the DROP policy in the iptables-legacy tables.
sudo update-alternatives --set iptables /usr/sbin/iptables-legacy >/dev/null 2>&1 || true
sudo update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy >/dev/null 2>&1 || true

# Allow the agent user to talk to the daemon without sudo.
sudo groupadd -f docker
sudo usermod -aG docker "$(id -un)" || true

# --- uv (Python 3.13 workspace) --------------------------------------------
if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
  log "Installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

# --- Local env files (never overwrite user edits) ---------------------------
[ -f .env ] || cp .env.example .env
if [ ! -f apps/web/.env ]; then
  log "Seeding apps/web/.env"
  cat > apps/web/.env <<'WEBENV'
# Local Expo web env. Required legal values (app throws if missing).
EXPO_PUBLIC_LEGAL_OPERATOR_NAME=Storecipe Ltd.
EXPO_PUBLIC_PRIVACY_CONTACT_EMAIL=privacy@storecipe.test
EXPO_PUBLIC_LEGAL_EFFECTIVE_DATE=2026-08-24

# Backend API bases served by the local Docker Compose stack.
EXPO_PUBLIC_CATALOG_API_URL=http://localhost:8000
EXPO_PUBLIC_INGESTION_API_URL=http://localhost:8001

# Auth0 SPA values (API audience). Leave empty for local infra checks;
# set these to enable real Universal Login against your dev Auth0 tenant.
EXPO_PUBLIC_AUTH0_DOMAIN=
EXPO_PUBLIC_AUTH0_CLIENT_ID=
EXPO_PUBLIC_AUTH0_AUDIENCE=
WEBENV
fi

# --- Python workspace dependencies -----------------------------------------
log "Syncing Python workspace (uv)"
uv sync --all-packages --group dev

# --- Web dependencies -------------------------------------------------------
log "Installing web dependencies (pnpm)"
(cd apps/web && pnpm install --frozen-lockfile)

# --- Pre-build Docker images so first boot is fast --------------------------
# Best-effort: bakes the service images into the environment build snapshot.
log "Starting Docker daemon to pre-build images"
sudo service docker start || true
for _ in $(seq 1 30); do
  if sudo docker info >/dev/null 2>&1; then break; fi
  sleep 1
done
if sudo docker info >/dev/null 2>&1; then
  sudo docker compose build
  # Bake the external base images (postgres, redis) into the snapshot too, so
  # the first boot does not need to pull from a registry.
  sudo docker compose pull postgres redis redis-broker
else
  echo "WARN: Docker daemon not available during install; images will build on first start."
fi

log "install.sh complete"
