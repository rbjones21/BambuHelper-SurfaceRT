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

echo "[6/6] Disabling screen blanking and power management..."
# --- X11 blanking off -------------------------------------------------------
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

# --- systemd: never suspend / hibernate -------------------------------------
# Tegra 3 cannot reliably resume from ACPI suspend or DPMS, so we mask every
# sleep target. Without this a default Debian install will eventually try to
# suspend the device and require a hard reset to recover.
systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target \
    >/dev/null 2>&1 || true

# --- logind: ignore lid / power key / idle ----------------------------------
mkdir -p /etc/systemd/logind.conf.d
cat > /etc/systemd/logind.conf.d/10-bambuhelper-nosleep.conf << 'EOF'
[Login]
HandleLidSwitch=ignore
HandleLidSwitchDocked=ignore
HandleLidSwitchExternalPower=ignore
HandlePowerKey=ignore
HandleSuspendKey=ignore
HandleHibernateKey=ignore
IdleAction=ignore
IdleActionSec=0
EOF

# --- kernel console blanker off ---------------------------------------------
# fbcon blanks the framebuffer after ~10 minutes independently of X. On Tegra
# this routes through the same display-controller power path that hangs the
# GPU. Run setterm redirected to /dev/tty1 *without* claiming the TTY (no
# StandardInput=tty / TTYPath=...), otherwise it races with the display
# manager / kiosk for ownership of tty1 and the kiosk fails to start.
cat > /etc/systemd/system/bambuhelper-noblank.service << 'EOF'
[Unit]
Description=BambuHelper - disable kernel console blanking
After=multi-user.target

[Service]
Type=oneshot
RemainAfterExit=yes
# Redirect to tty1 without owning it, so getty/LightDM/X are not disturbed.
ExecStart=/bin/sh -c '/bin/setterm --blank 0 --powerdown 0 --powersave off >/dev/tty1 2>/dev/null || true'

[Install]
WantedBy=multi-user.target
EOF
systemctl enable bambuhelper-noblank.service >/dev/null 2>&1 || true

# --- Pin DRM + backlight runtime PM to "on" ---------------------------------
# The kernel may autosuspend the display controller / backlight device. On
# Tegra 3 that is the same hang risk as DPMS. udev rules force runtime PM off
# for those devices at boot and on hotplug.
cat > /etc/udev/rules.d/99-bambuhelper-nopm.rules << 'EOF'
# Force display-related devices to stay powered
SUBSYSTEM=="backlight", ATTR{power/control}="on"
SUBSYSTEM=="drm",       ATTR{power/control}="on"
SUBSYSTEM=="graphics",  ATTR{power/control}="on"
EOF

systemctl daemon-reload

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
