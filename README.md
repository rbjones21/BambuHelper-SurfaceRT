# BambuHelperRT — v1.1.0

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
- **Kiosk**: Chromium launched fullscreen via LXDE autostart

---

## Architecture

```
Bambu Cloud MQTT → bambu_server.py (Flask + SocketIO :5000) → WebSocket → Chromium kiosk
```

- `bambu_server.py` — Python MQTT client + Flask/SocketIO server
- `templates/dashboard.html` — Live dashboard UI
- `templates/settings.html` — Settings page
- `/etc/bambuhelper/config.json` — Printer config (never overwritten by updates)

---

## Dashboard Features

### Per-printer panel
- **Status icon** — SVG icons: play (printing), pause bars (paused), green checkmark (finished), red exclamation (error)
- **Printer name** with live connection dot (green/red/amber)
- **Status badge** — Printing / Paused / Finished / Failed / Idle / Offline
- **Print name** — filename of the active print job
- **Layer count** — current layer / total layers
- **Remaining time** — time left in the print
- **Printer action status** — live stage from the printer: Printing, Changing filament, Auto bed leveling, Heating nozzle, Home toolhead, etc.
- **ETA** — estimated finish time (12h or 24h format, configurable)
- **LED progress bar** — H2-style 40-segment bar showing completion %
- **6 arc gauges** — Nozzle temp, Bed temp, Chamber temp, Part Fan %, Aux Fan %, Exhaust Fan %

### Layout
- **Two printers**: side-by-side split layout
- **One printer** (or one disabled): single panel expands full width with larger fonts, bigger gauges laid out in a single row

### Header
- BambuHelperRT logo
- WiFi signal strength (mlan0)
- Clock (12h or 24h)
- Settings button

---

## Settings Page

### Printer tabs (Printer 1 / Printer 2)
- Printer name
- Connection mode: LAN or Cloud
- Serial number
- LAN: IP address and access code
- Cloud: region (US/CN), Bambu user ID, access token
- Enable/disable toggle — disabled printers are hidden from the dashboard

### Display
- **Brightness** slider
- **Rotation** — Normal / Left / Right / Inverted
- **Display off after print** — minutes after all prints finish before screen turns off (0 = never)
- **Always on** — override timeout, keep screen on permanently
- **Show clock after print** — keep screen on showing clock after print completes
- **Time format** — 12 hour or 24 hour

### Gauge Colors
- **Theme presets**: Default (dark cyan), Bambu (light grey + green), Mono Green, Neon, Warm, Ocean
- **Per-gauge color pickers** for arc color, label color, and value color
- Changes apply to the dashboard immediately on save

### About
- Project description and links

---

## Connection Modes

### Cloud Mode (H2D, H2C, H2S, P2S)
Connects via Bambu Lab's cloud MQTT broker. Does not require Developer Mode on the printer.

```json
{
  "id": "printer1",
  "name": "FDS H2D",
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

All Bambu Lab printers use the same MQTT protocol and field names, so BambuHelperRT should work with any model. The fields we parse (`nozzle_temper`, `bed_temper`, `gcode_state`, `mc_percent`, `stg_cur`, `spd_lvl`, etc.) are present across all series.

| Series | Cloud Mode | LAN Mode | Notes |
|---|---|---|---|
| H2D, H2C, H2S | ✅ Tested | ✅ With Dev Mode | Primary test platform |
| X1C, X1E | ✅ Should work | ✅ With Dev Mode | Same MQTT fields |
| P1S, P1P | ✅ Should work | ✅ With Dev Mode | P1 sends delta updates only — pushall request handles this |
| A1, A1 Mini | ✅ Should work | ✅ With Dev Mode | Same protocol |
| P2S | ✅ Should work | ✅ With Dev Mode | Newer series, same fields |

**Note on P1 series in LAN mode:** The P1P/P1S only send changed fields in each MQTT message rather than the full state. BambuHelperRT handles this correctly since it merges incoming fields into the existing state rather than replacing it entirely.

If you test with a printer not listed as "Tested" above and it works, feel free to open an issue on GitHub to confirm so the table can be updated.

---

## Security Notes

The web server binds to `127.0.0.1` (localhost only) — it is only accessible from Chromium running on the Surface RT itself, not from other devices on your network. This means your Bambu credentials stored in config.json are not exposed to the network.

If you need to access the dashboard from another device on your network (e.g. a phone or PC), you can change the bind address in `bambu_server.py` from `127.0.0.1` to `0.0.0.0`, but be aware this will expose all API endpoints including `/api/config` which contains your printer tokens. Only do this on a trusted private network.

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
scp -r bambuhelper-surface-v2/ rjones@<SURFACE_RT_IP>:/tmp/
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
| `/usr/local/bin/bambu-update` | Updater script |
| `/usr/local/bin/bambu-rollback` | Rollback script |
| `/etc/systemd/system/bambuhelper.service` | Server systemd service |
| `/etc/systemd/system/bambuhelper-kiosk.service` | Chromium kiosk service |

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
```

---

## Troubleshooting

| Problem | Solution |
|---|---|
| Service won't start (KeyError: 'id') | config.json is missing `id` field — restore manually with correct JSON |
| Cloud printer offline | Token expired — get fresh token from bambulab.com cookies |
| Dashboard blank (no panels) | JavaScript error — check browser console or `journalctl -u bambuhelper -f` |
| Colors not applying | Settings save may have dropped `id` field — check config.json |
| Screen not turning off | Check timeout is not 0 and "Always on" is unchecked in settings |
| bambu-update fails | Run `curl -v --max-time 30 https://raw.githubusercontent.com/rbjones21/BambuHelper-SurfaceRT/main/version.txt` to test connectivity |

---

## Changelog

### v1.1.0 — March 2026
- Dynamic color themes including Bambu theme (light grey + green)
- Per-gauge color pickers in settings
- SVG status icons (printing / paused / finished / error)
- Live printer action status (Changing filament, Auto bed leveling, Home toolhead, etc.)
- Large ETA display with 12h/24h format toggle in settings
- Single printer expands to full width with larger UI
- Display timeout and always-on settings now functional (controls screen via xset)
- Disabled printers hidden from dashboard and WebSocket broadcast
- Fixed updater URL (was hardcoded with placeholder username)
- Fixed duplicate Flask routes causing control buttons to fail
- Fixed missing `request` and `Response` Flask imports
- Fixed `id` field crash on startup — server now auto-assigns if missing
- Chromium refreshes automatically after bambu-update (no reboot needed)
- **Security:** Server now binds to localhost only (127.0.0.1) — not exposed to network
- **Security:** Display rotation parameter validated against whitelist

### v1.0.0 — March 2026
- Initial release
- Dual printer MQTT monitoring via Bambu Cloud
- Arc gauges for temperature and fans
- LED progress bar
- Settings page with printer config and display controls
- GitHub-based OTA updater

---

*BambuHelperRT — [github.com/rbjones21/BambuHelper-SurfaceRT](https://github.com/rbjones21/BambuHelper-SurfaceRT)*

---

## v1.2.0 — Planned

- **Idle clock display** — when no printer is actively printing, replace the printer panel(s) with a full-screen clock and date, matching the original BambuHelper ESP32 behavior
- **Chromium tab accumulation fix** — kiosk autostart opens a new tab on each boot rather than reusing the existing session; fix to ensure only one tab/window is ever open
- **Network status false offline** — the WiFi signal indicator in the header frequently shows offline incorrectly; fix the `iwconfig mlan0` parsing or polling logic
- **Settings system buttons** — add Reboot, Shutdown, and Open Terminal buttons to the settings page (under a new System section)
- **Settings theme consistency** — the settings page does not reflect the currently active dashboard theme; both pages should share the same color scheme when a theme is applied
