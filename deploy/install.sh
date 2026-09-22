#!/bin/sh
# Install BlockChores on a Debian 13 box.  Run from the repository root:
#
#   sudo sh deploy/install.sh
#
set -eu

APP_DIR=/opt/blockchores
SRC_DIR=$(cd "$(dirname "$0")/.." && pwd)

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this with sudo." >&2
    exit 1
fi

command -v python3 >/dev/null 2>&1 || { echo "python3 is required (apt install python3)" >&2; exit 1; }

echo "Installing from $SRC_DIR to $APP_DIR"
mkdir -p "$APP_DIR"
cp -r "$SRC_DIR/server.py" "$SRC_DIR/static" "$APP_DIR/"
chmod -R a+rX "$APP_DIR"

cp "$SRC_DIR/deploy/blockchores.service" /etc/systemd/system/blockchores.service
systemctl daemon-reload
systemctl enable --now blockchores

sleep 1
systemctl --no-pager --lines=5 status blockchores || true

ADDR=$(hostname -I 2>/dev/null | awk '{print $1}')
echo
echo "Done.  Open this on the iPad:  http://${ADDR:-<server-ip>}:8080/"
echo "Data lives in /var/lib/blockchores/chores.json"
