#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "install_host.sh must run as root" >&2
  exit 1
fi

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
DATA_DEVICE=${STORECIPE_DATA_DEVICE:-/dev/disk/by-id/google-storecipe-data}
DATA_MOUNT=/var/lib/storecipe

until [[ -b "$DATA_DEVICE" ]]; do sleep 2; done
if ! blkid "$DATA_DEVICE" >/dev/null 2>&1; then mkfs.ext4 -F "$DATA_DEVICE"; fi
install -d -m 0750 -o root -g root "$DATA_MOUNT"
grep -qF "$DATA_DEVICE $DATA_MOUNT" /etc/fstab || \
  echo "$DATA_DEVICE $DATA_MOUNT ext4 defaults,nofail 0 2" >> /etc/fstab
mountpoint -q "$DATA_MOUNT" || mount "$DATA_MOUNT"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl gnupg jq openssl postgresql-client python3
if ! command -v gcloud >/dev/null 2>&1; then
  curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \
    | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg
  echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
    > /etc/apt/sources.list.d/google-cloud-sdk.list
  apt-get update
  apt-get install -y google-cloud-cli
fi
install -m 0755 -d /etc/apt/keyrings
if [[ ! -f /etc/apt/keyrings/docker.asc ]]; then
  curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
fi
if [[ ! -f /etc/apt/sources.list.d/docker.list ]]; then
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $VERSION_CODENAME stable" > /etc/apt/sources.list.d/docker.list
  apt-get update
fi
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

install -d -m 0750 -o root -g root \
  /opt/storecipe/releases /opt/storecipe/current /run/storecipe "$DATA_MOUNT/docker"
install -d -m 0755 /etc/docker
printf '%s\n' '{"data-root":"/var/lib/storecipe/docker","log-driver":"json-file","log-opts":{"max-size":"10m","max-file":"3"}}' > /etc/docker/daemon.json
systemctl enable --now docker
systemctl restart docker

if [[ ! -e /swapfile ]]; then
  fallocate -l 2G "$DATA_MOUNT/swapfile"
  chmod 0600 "$DATA_MOUNT/swapfile"
  mkswap "$DATA_MOUNT/swapfile"
  ln -s "$DATA_MOUNT/swapfile" /swapfile
fi
swapon --show=NAME | grep -qx "$DATA_MOUNT/swapfile" || swapon "$DATA_MOUNT/swapfile"
grep -qF "$DATA_MOUNT/swapfile none swap" /etc/fstab || \
  echo "$DATA_MOUNT/swapfile none swap sw 0 0" >> /etc/fstab

install -m 0644 "$ROOT_DIR/infra/systemd/storecipe-backup.service" /etc/systemd/system/
install -m 0644 "$ROOT_DIR/infra/systemd/storecipe-backup.timer" /etc/systemd/system/
install -m 0644 "$ROOT_DIR/infra/systemd/storecipe-media-reconcile.service" /etc/systemd/system/
install -m 0644 "$ROOT_DIR/infra/systemd/storecipe-media-reconcile.timer" /etc/systemd/system/
install -m 0750 "$ROOT_DIR/scripts/deploy/run_with_runtime_env.sh" /usr/local/sbin/storecipe-runtime-operation
systemctl daemon-reload
systemctl enable --now storecipe-backup.timer storecipe-media-reconcile.timer

echo "Storecipe host installation complete."
