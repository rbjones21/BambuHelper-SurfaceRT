# BambuHelperRT — v1.3.0

A Bambu Lab printer monitor dashboard running on a **Microsoft Surface RT** with Debian 12.
Connects to one or two printers simultaneously via Bambu Cloud MQTT and displays live status
on the Surface RT's built-in screen in kiosk mode.

Inspired by [Keralots/BambuHelper](https://github.com/Keralots/BambuHelper) — an ESP32-based
Bambu printer monitor — this project ports the concept to a full Linux computer, adding a
web-based dashboard, settings UI, and multi-printer support.

For info on running Debian 12 on a Surface RT:
[open-rt.gitbook.io](https://open-rt.gitbook.io/open-surfacert/surface-rt/linux)

---

## Hardware

- **Device**: Microsoft Surface RT (Tegra 3, armhf) running Debian 12
- **Display**: Surface RT built-in 10.6" 1366×768 touchscreen
- **Network**: WiFi (mlan0), power management disabled for stability
- **Kiosk**: Chromium launched fullscreen via systemd service

---

## Architecture

```
Bambu Cloud MQTT → bambu_server.py (Flask + SocketIO :5000) → WebSocket → Chromium kiosk
```

- `bambu_server.py` — Python MQTT client + Flask/SocketIO server
- `templates/dashboard.html` — Live dashboard UI
- `templates/settings.html` — Settings page
- `/etc/bambuhelper/config.json` — Printer config (never overwritten by updates)
- `/etc/bambuhelper/config.known-good.json` — Auto-backup of last working config

---

## Dashboard Features

### Per-printer panel
- **Status icon** — SVG icons: play (printing), pause bars (paused), green checkmark (finished), red exclamation (error)
- **Printer name** with live connection dot (green/red/amber)
- **Status badge** — Printing / Paused / Finished / Failed / Idle / Offline
- **Print name** — filename of the active print job
- **Layer count** — current layer / total layers
- **Remaining time** — time left in the print
- **Printer action status** — live stage: Printing, Changing filament, Auto bed leveling, Heating nozzle, Home toolhead, etc.
- **ETA** — estimated finish time (12h or 24h format)
- **LED progress bar** — H2-style 40-segment bar showing completion %
- **6 arc gauges** — Nozzle temp, Bed temp, Chamber temp, Part Fan %, Aux Fan %, Exhaust Fan %
- **Nozzle type** — active nozzle type shown under nozzle gauge (e.g. HS01-0.4, HH01)
- **Virtual slot dots** — two colored dots under nozzle gauge showing left/right nozzle filament colors
- **AMS strip** — filament color swatches for all AMS slots with remaining %, active tray highlighted, empty slots faded

### Layout
- **Two printers**: side-by-side split layout
- **One printer** (or one disabled): single panel expands full width with larger fonts and gauges in a single row
- **No active prints**: full-screen idle clock with date display

### Header
- BambuHelperRT logo
- WiFi signal strength
- Clock (12h or 24h)
- Refresh button
- Settings button

---

## Settings Page

All cards start collapsed — tap a card header to expand it.

### Printer tabs (Printer 1 / Printer 2)
- Printer name
- Connection mode: LAN or Cloud
- Serial number
- LAN: IP address and access code
- Cloud: region (US/CN), Bambu user ID, access token
- Enable/disable toggle — disabled printers are hidden from the dashboard

### Display
- **Brightness** slider — controls Surface RT backlight directly
- **Display off after print** — minutes after all prints finish before screen turns off (0 = never)
- **Always on** — override timeout, keep screen on permanently
- **Show clock after print** — show idle clock while waiting for timeout
- **Time format** — 12 hour or 24 hour

### Gauge Colors
- **Theme presets**: Default (dark cyan), Bambu (light grey + green), Mono Green, Neon, Warm, Ocean
- **Per-gauge color pickers** for arc, label, and value colors
- Theme applies to both dashboard and settings page

### Network
- **Current connection** — shows active SSID and IP address
- **WiFi scan** — lists nearby networks with signal strength and security type
- **Connect** — tap any network to enter password and connect; falls back to previous network on failure
- **Forget** — removes saved credentials for a network
- **IP Configuration** — switch between DHCP and Static IP; static mode shows fields for IP/subnet, gateway, and DNS

### System
- **Timezone** — select from common timezones; DST handled automatically by the system
- **Reboot** — reboots the Surface RT (with confirmation)
- **Shutdown** — shuts down the Surface RT (with confirmation)
- **Terminal** — opens an xterm window on the display

### About
- Project description and links

---

## AMS Support

AMS tray data is parsed from the Bambu Cloud MQTT stream and displayed as a color strip at
the bottom of each printer panel.

- **Filament colors** — actual spool colors from AMS RFID tags
- **Remaining %** — percentage of filament remaining per slot (when reported by AMS)
- **Active tray** — highlighted with accent color and `▶` when state=24 (actively feeding)
- **In-job trays** — subtly outlined when part of the current print job
- **Empty slots** — faded to indicate no filament loaded
- **Multiple AMS units** — supports up to 2 AMS units (8 trays) per printer

> **Note:** Due to cloud MQTT limitations, simultaneous left/right nozzle temperatures are
> not available for H2D/H2C in cloud mode. Only the active nozzle temperature is reported.
> Active tray detection (state=24) fires briefly during filament changes; in-job highlighting
> uses the mapping field to show which slots are assigned to the current print.

---

## Connection Modes

### Cloud Mode (H2D, H2C, H2S, P2S)
Connects via Bambu Lab's cloud MQTT broker. Does not require Developer Mode.

```json
{
  "id": "printer1",
  "name": "My Printer",
  "mode": "cloud",
  "region": "us",
  "serial": "YOUR_SERIAL",
  "bambu_user_id": "YOUR_USER_ID",
  "bambu_token": "YOUR_TOKEN",
  "enabled": true
}
```

**Getting your token:**
1. Log into [bambulab.com](https://bambulab.com) in your browser
2. Open Developer Tools (F12) → Application → Cookies → bambulab.com
3. Copy the `token` cookie value as `bambu_token`
4. Find your user ID at: `https://bambulab.com/api/v1/design-user-service/my/preference` — look for `uid`

> Tokens expire every ~3 months. When a printer shows offline and logs show "Bad credentials", get a fresh token and update it in Settings.

### LAN Mode (X1, P1, A1 series with Developer Mode)
Connects directly to the printer on your local network.

```json
{
  "id": "printer1",
  "name": "My Printer",
  "mode": "lan",
  "ip": "192.168.1.100",
  "serial": "YOUR_SERIAL",
  "access_code": "YOUR_8_CHAR_CODE",
  "enabled": true
}
```

Requires Developer Mode: Settings → General → Developer Mode on the printer touchscreen.

---

## Printer Compatibility

All Bambu Lab printers use the same MQTT protocol and field names.

| Series | Cloud Mode | LAN Mode | Notes |
|---|---|---|---|
| H2D, H2C, H2S | ✅ Tested | ✅ With Dev Mode | Primary test platform |
| X1C, X1E | ✅ Should work | ✅ With Dev Mode | Same MQTT fields |
| P1S, P1P | ✅ Should work | ✅ With Dev Mode | P1 sends delta updates only |
| A1, A1 Mini | ✅ Should work | ✅ With Dev Mode | Same protocol |
| P2S | ✅ Should work | ✅ With Dev Mode | Newer series, same fields |

---

## Updater

```bash
# Check for updates
sudo bambu-update --check

# Apply update (preserves config.json)
sudo bambu-update

# Force reinstall current version
sudo bambu-update --force

# Roll back to previous version
sudo bambu-rollback
```

The updater pulls `bambu_server.py`, `templates/dashboard.html`, `templates/settings.html`,
and `version.txt` from this GitHub repo. **config.json is never overwritten.**

---

## Installation

### 1. Transfer files to the Surface RT

```bash
scp -r bambuhelper-surface-v2/ user@<SURFACE_RT_IP>:/tmp/
```

### 2. Run the installer

```bash
cd /tmp/bambuhelper-surface-v2
sudo bash install.sh
```

### 3. Edit the config

```bash
sudo nano /etc/bambuhelper/config.json
sudo systemctl restart bambuhelper
```

Or use the Settings page in the dashboard.

---

## File Locations

| Path | Purpose |
|---|---|
| `/opt/bambuhelper/bambu_server.py` | Main server (updated by bambu-update) |
| `/opt/bambuhelper/templates/` | Dashboard and settings HTML |
| `/opt/bambuhelper/venv/` | Python virtual environment |
| `/etc/bambuhelper/config.json` | Printer and display config (never overwritten) |
| `/etc/bambuhelper/config.known-good.json` | Auto-backup of last working config |
| `/usr/local/bin/bambu-update` | Updater script |
| `/usr/local/bin/bambu-rollback` | Rollback script |
| `/etc/systemd/system/bambuhelper.service` | Server systemd service |
| `/etc/systemd/system/bambuhelper-kiosk.service` | Chromium kiosk service |
| `/etc/udev/rules.d/99-backlight.rules` | Backlight write permissions |
| `/etc/sudoers.d/bambuhelper` | Passwordless reboot/shutdown/nmcli/timedatectl |

---

## Useful Commands

```bash
# Live logs
journalctl -u bambuhelper -f

# Restart server
sudo systemctl restart bambuhelper

# Stop kiosk to access desktop
sudo systemctl stop bambuhelper-kiosk

# Check printer state via API
curl -s http://localhost:5000/api/state | python3 -m json.tool

# Check display config
curl -s http://localhost:5000/api/display | python3 -m json.tool

# Check network status
curl -s http://localhost:5000/api/network/status | python3 -m json.tool

# Set brightness manually (0-254)
echo 128 | sudo tee /sys/class/backlight/backlight/brightness

# Check WiFi signal
iw dev mlan0 link
```

---

## Security Notes

The web server binds to `127.0.0.1` (localhost only) — accessible only from Chromium
on the Surface RT itself, not from other devices on your network.

If you need remote access, change `host='127.0.0.1'` to `host='0.0.0.0'` in
`bambu_server.py`, but be aware this exposes `/api/config` which contains your printer tokens.

---

## Troubleshooting

| Problem | Solution |
|---|---|
| Service won't start (KeyError: 'id') | Config corrupted — server auto-restores from known-good backup on next restart |
| Cloud printer offline | Token expired — get fresh token from bambulab.com cookies |
| Dashboard blank | JavaScript error — restart service and hard-refresh Chromium |
| Colors not applying | Open Settings, select theme and click Apply |
| Screen not turning off | Check timeout > 0 and "Always on" is unchecked |
| Brightness slider has no effect | Check `/sys/class/backlight/backlight/brightness` is writable |
| Network scan fails | Check nmcli is in sudoers: `sudo cat /etc/sudoers.d/bambuhelper` |
| Static IP not applying | Ensure nmcli has sudo access and the SSID name matches exactly |
| AMS not showing | AMS data only appears after first MQTT pushall (~30s after connect) |
| bambu-update fails | Run `curl -v --max-time 30 https://raw.githubusercontent.com/rbjones21/BambuHelper-SurfaceRT/main/version.txt` |

---

## Changelog

### v1.3.0 — March 2026
- **Network settings** — WiFi scan, connect to new networks, forget saved networks, all from the settings UI
- **Static IP / DHCP** — switch between DHCP and static IP configuration via settings; supports IP, gateway, and DNS
- **Timezone selector** — choose timezone from common presets in Settings → System; DST handled automatically
- **AMS display** — filament color strip at bottom of each printer panel showing all AMS slots with remaining %, active tray indicator, and in-job highlighting
- **Nozzle type** — active nozzle type (e.g. HS01-0.4, HH01) shown under nozzle gauge label
- **Virtual slot dots** — two small colored dots showing left/right nozzle filament colors from Bambu's virtual slot data
- **Rotation removed** — xrandr not supported on Surface RT framebuffer; all rotation code removed
- **Code cleanup** — both HTML templates fully reformatted with section comments; settings page opens with all cards collapsed

### v1.2.0 — March 2026
- **Idle clock** — full-screen clock and date when no prints are active, auto-returns to printer panels when printing starts
- **Single Chromium instance** — fixed duplicate tab/window on boot by switching from LXDE autostart to systemd service
- **Network status fix** — switched from `iwconfig` (unsupported) to `iw dev mlan0 link` for reliable signal strength
- **System buttons** — Reboot, Shutdown, and Terminal buttons in Settings → System
- **Theme consistency** — settings page now matches dashboard theme when changed
- **Brightness control** — slider now controls Surface RT backlight via `/sys/class/backlight`
- **Config protection** — auto-backup to `config.known-good.json`, auto-restore on corrupt config, id field always repaired before save
- **MQTT stability** — old workers properly disconnected before restarting on config save
- **Thumbnail spam fixed** — thumbnails only fetched when actively printing, failures cached per print job
- **Security** — server now binds to localhost only (127.0.0.1)
- **Code cleanup** — all imports at top level, STAGE_MAP moved to module level, duplicate code removed

### v1.1.0 — March 2026
- Dynamic color themes including Bambu theme (light grey + green)
- Per-gauge color pickers in settings
- SVG status icons (printing / paused / finished / error)
- Live printer action status (Changing filament, Auto bed leveling, Home toolhead, etc.)
- Large ETA display with 12h/24h format toggle in settings
- Single printer expands to full width with larger UI
- Display timeout and always-on settings functional
- Disabled printers hidden from dashboard and WebSocket broadcast
- Fixed updater URL, duplicate Flask routes, missing imports
- Chromium refreshes automatically after bambu-update

### v1.0.0 — March 2026
- Initial release
- Dual printer MQTT monitoring via Bambu Cloud
- Arc gauges for temperature and fans
- LED progress bar
- Settings page with printer config and display controls
- GitHub-based OTA updater

---

*BambuHelperRT — [github.com/rbjones21/BambuHelper-SurfaceRT](https://github.com/rbjones21/BambuHelper-SurfaceRT)*
