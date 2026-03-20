#!/bin/bash
# BambuHelper Surface RT v2 - Install Script
# Run as root: sudo bash install.sh

set -e

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  BambuHelper Surface RT v2 Installer     ║"
echo "║  LAN + Cloud Mode Support                ║"
echo "╚══════════════════════════════════════════╝"
echo ""

echo "[1/6] Installing system packages..."
apt-get update -q
apt-get install -y \
    python3 python3-pip python3-venv \
    chromium unclutter xdotool \
    --no-install-recommends

echo "[2/6] Setting up application directory..."
mkdir -p /opt/bambuhelper/templates
mkdir -p /etc/bambuhelper

echo "[3/6] Copying application files..."
cp bambu_server.py /opt/bambuhelper/
cp templates/dashboard.html /opt/bambuhelper/templates/
cp templates/settings.html /opt/bambuhelper/templates/
cp config.cloud-example.json /opt/bambuhelper/
cp version.txt /opt/bambuhelper/

# Install update and rollback commands globally
cp bambu-update   /usr/local/bin/bambu-update
cp bambu-rollback /usr/local/bin/bambu-rollback
chmod +x /usr/local/bin/bambu-update
chmod +x /usr/local/bin/bambu-rollback
echo "      Update commands installed: bambu-update, bambu-rollback"

if [ ! -f /etc/bambuhelper/config.json ]; then
    cp config.json /etc/bambuhelper/config.json
    echo "      Config created at /etc/bambuhelper/config.json"
    echo "      *** Edit this with your printer details before starting! ***"
else
    echo "      Config already exists — preserving your settings"
    echo "      See config.cloud-example.json for cloud mode reference"
fi

echo "[4/6] Creating Python virtual environment..."
python3 -m venv /opt/bambuhelper/venv
/opt/bambuhelper/venv/bin/pip install --upgrade pip -q
/opt/bambuhelper/venv/bin/pip install \
    flask \
    flask-socketio \
    paho-mqtt \
    simple-websocket \
    -q
echo "      Python dependencies installed"

echo "[5/6] Installing systemd services..."
cp bambuhelper.service /etc/systemd/system/
cp bambuhelper-kiosk.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable bambuhelper.service
systemctl enable bambuhelper-kiosk.service

echo "[6/6] Disabling screen blanking..."
mkdir -p /etc/X11/xorg.conf.d
cat > /etc/X11/xorg.conf.d/10-blanking.conf << 'EOF'
Section "ServerFlags"
    Option "BlankTime"   "0"
    Option "StandbyTime" "0"
    Option "SuspendTime" "0"
    Option "OffTime"     "0"
EndSection
EOF

mkdir -p /etc/xdg/autostart
cat > /etc/xdg/autostart/unclutter.desktop << 'EOF'
[Desktop Entry]
Type=Application
Name=Unclutter
Exec=unclutter -idle 1 -root
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
EOF

echo ""
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║                  Installation Complete!                   ║"
echo "╠═══════════════════════════════════════════════════════════╣"
echo "║                                                           ║"
echo "║  1. Edit your printer config:                             ║"
echo "║     nano /etc/bambuhelper/config.json                     ║"
echo "║                                                           ║"
echo "║  2. For LAN mode set:  ip, serial, access_code            ║"
echo "║     For cloud mode set: serial, bambu_user_id,            ║"
echo "║                         bambu_token, region               ║"
echo "║                                                           ║"
echo "║  3. See cloud mode help:                                  ║"
echo "║     cat /opt/bambuhelper/config.cloud-example.json        ║"
echo "║     or README.md                                          ║"
echo "║                                                           ║"
echo "║  4. Start the service:                                    ║"
echo "║     sudo systemctl start bambuhelper                      ║"
echo "║                                                           ║"
echo "║  5. Watch the logs:                                       ║"
echo "║     journalctl -u bambuhelper -f                          ║"
echo "║                                                           ║"
echo "║  Or just reboot — everything starts automatically.        ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""
