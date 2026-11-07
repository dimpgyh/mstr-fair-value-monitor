#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Cara Pakai: ./push-to-vps.sh <IP_VPS_BIZNET_GIO> [USER_SSH] [PORT_SSH]"
  echo "Contoh    : ./push-to-vps.sh 103.127.98.10 root 22"
  exit 1
fi

VPS_IP="$1"
VPS_USER="${2:-root}"
SSH_PORT="${3:-22}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "============================================================"
echo "  Mengunggah & Men-setup MSTR Monitor ke ${VPS_USER}@${VPS_IP} "
echo "============================================================"

ssh -p "${SSH_PORT}" -o StrictHostKeyChecking=accept-new "${VPS_USER}@${VPS_IP}" "mkdir -p /tmp/mstr-fair-value-monitor"

scp -P "${SSH_PORT}" -r \
  "${SCRIPT_DIR}/app" \
  "${SCRIPT_DIR}/requirements.txt" \
  "${SCRIPT_DIR}/Dockerfile" \
  "${SCRIPT_DIR}/docker-compose.yml" \
  "${SCRIPT_DIR}/deploy-biznetgio.sh" \
  "${VPS_USER}@${VPS_IP}:/tmp/mstr-fair-value-monitor/"

ssh -p "${SSH_PORT}" -t "${VPS_USER}@${VPS_IP}" "bash /tmp/mstr-fair-value-monitor/deploy-biznetgio.sh"
