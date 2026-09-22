#!/bin/sh
# Install BlockChores on a Debian box.  Run from the repository root:
#
#   sudo sh deploy/install.sh
#
# Re-run it any time to upgrade to the latest checkout; your chores are kept
# in /var/lib/blockchores and are never touched by this script.
#
# Override the defaults if you need to:
#
#   sudo PORT=80 sh deploy/install.sh
#
set -eu

APP_DIR="${APP_DIR:-/opt/blockchores}"
UNIT_DIR="${UNIT_DIR:-/etc/systemd/system}"
PORT="${PORT:-8080}"
SRC_DIR=$(cd "$(dirname "$0")/.." && pwd)

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this with sudo." >&2
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required:  sudo apt install python3" >&2
    exit 1
fi

echo "Installing from $SRC_DIR to $APP_DIR (port $PORT)"

# Replace the code outright rather than copying over it, so files deleted
# upstream do not linger and a re-run cannot nest static/ inside itself.
mkdir -p "$APP_DIR"
rm -rf "$APP_DIR/static"
cp "$SRC_DIR/server.py" "$APP_DIR/server.py"
cp -r "$SRC_DIR/static" "$APP_DIR/static"
chmod -R a+rX "$APP_DIR"

sed -e "s|/opt/blockchores|$APP_DIR|g" \
    -e "s|--port 8080|--port $PORT|" \
    "$SRC_DIR/deploy/blockchores.service" > "$UNIT_DIR/blockchores.service"

if [ ! -d /run/systemd/system ]; then
    echo
    echo "systemd is not running here, so the service was not started."
    echo "The unit is in place at $UNIT_DIR/blockchores.service."
    exit 0
fi

systemctl daemon-reload
systemctl enable blockchores >/dev/null
systemctl restart blockchores          # restart, so a re-run picks up new code

# Give it a moment, then say plainly whether it came up.
sleep 1
if ! systemctl is-active --quiet blockchores; then
    echo
    echo "The service failed to start.  The log says:" >&2
    journalctl -u blockchores --no-pager --lines=20 >&2
    exit 1
fi

ADDR=$(hostname -I 2>/dev/null | awk '{print $1}')
echo
echo "BlockChores is running."
echo "  On the iPad open:  http://${ADDR:-<server-ip>}:$PORT/"
echo "  Your chores live in:  /var/lib/blockchores/chores.json"
echo "  Watch the log with:   journalctl -u blockchores -f"
