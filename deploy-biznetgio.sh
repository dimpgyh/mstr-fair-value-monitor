#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/mstr-fair-value-monitor"
PORT="${PORT:-8000}"

echo "============================================================"
echo "  Instalasi Otomatis MSTR Fair Value Monitor (Biznet Gio)   "
echo "============================================================"

if [ "$EUID" -ne 0 ]; then
  echo "[INFO] Menjalankan ulang dengan sudo..."
  exec sudo -E bash "$0" "$@"
fi

# 1. Pastikan paket dasar tersedia
apt-get update -y
apt-get install -y python3 python3-venv python3-pip curl ufw

# 2. Salin kode aplikasi ke /opt/mstr-fair-value-monitor jika belum di sana
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ "$SCRIPT_DIR" != "$APP_DIR" ]; then
  mkdir -p "$APP_DIR"
  cp -r "$SCRIPT_DIR"/* "$APP_DIR"/
fi

cd "$APP_DIR"
mkdir -p "$APP_DIR/data"

# 3. Buat Python Virtual Environment & Install Dependencies
echo "[INFO] Membuat Python virtual environment & menginstal paket..."
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

# 4. Buat Systemd Service agar jalan otomatis 24/7 & auto-start saat VPS reboot
cat >/etc/systemd/system/mstr-monitor.service <<EOF
[Unit]
Description=MSTR Fair Value Monitor (Auto-Refresh 1 Jam)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=${APP_DIR}
Environment="TZ=Asia/Jakarta"
Environment="DATA_DIR=${APP_DIR}/data"
ExecStart=${APP_DIR}/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable mstr-monitor.service
systemctl restart mstr-monitor.service

# 5. Buka firewall UFW jika aktif
if command -v ufw >/dev/null 2>&1; then
  ufw allow "${PORT}/tcp" >/dev/null 2>&1 || true
fi

PUBLIC_IP="$(curl -s --max-time 5 https://ifconfig.me || hostname -I | awk '{print $1}')"

echo ""
echo "============================================================"
echo "  INSTALASI BERHASIL! MSTR MONITOR SUDAH AKTIF 24/7         "
echo "============================================================"
echo "  URL Dashboard : http://${PUBLIC_IP}:${PORT}"
echo "  Status Service: systemctl status mstr-monitor"
echo "  Catatan       : Pastikan Port ${PORT} (TCP) sudah di-Allow"
echo "                  di menu Firewall / Security Group Biznet Gio."
echo "============================================================"
