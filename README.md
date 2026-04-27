# BambuHelperRT — v1.7.12

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
- **Dual nozzle display** — L and R nozzle temp bars shown side-by-side for H2D and H2C; active nozzle highlighted with `*`; reads packed 32-bit temps from `extruder` object (found inside `device` sub-object in cloud MQTT)
- **Nozzle type** — active nozzle type shown under nozzle gauge (e.g. HS01-0.4, HH01)
- **Virtual slot dots** — two colored dots under nozzle gauge showing left/right nozzle filament colors
- **AMS strip** — filament color swatches for all AMS slots with remaining %, active tray highlighted, empty slots faded (detects states 10, 24, and untyped slots)

### Layout
- **Two printers**: side-by-side split layout
- **One printer** (or one disabled): single panel expands full width with larger fonts and gauges in a single row
- **No active prints**: full-screen idle clock with date and current weather conditions

### Header
- BambuHelperRT logo
- WiFi signal strength
- Clock (12h or 24h)
- Refresh button
- Settings button
- **Update notification bar** — blue banner appears when a new version is available on GitHub; links directly to the About section in Settings for one-tap update
- **Token expiry banner** — orange banner when any cloud printer token is within 30 days of expiry

### Battery / Power
- **Battery indicator** — shows current battery percentage and charging state
- **Charging bolt icon** — ⚡ shown when plugged in; "Full" text displayed when charged to 95%+

### HMS Error Display
- **HMS error strip** — active HMS codes shown below each printer panel with human-readable descriptions
- **Tap to view** — tap any HMS entry to see the error code and QR link to the Bambu Lab wiki troubleshooting page
- **Dismiss button** — ✕ button to dismiss stale HMS codes; dismissed codes persist across MQTT heartbeats until the printer stops reporting them
- **Local fallback table** — 60+ common HMS codes resolved instantly without cloud API lookup
- **Cloud API enrichment** — unknown codes queried from Bambu's cloud HMS API and cached

### Offline Detection
- **Grace period** — 8-second grace period before showing "Offline" to prevent false flashes during MQTT reconnects
- **120-second staleness timeout** — printers marked offline only after 2 minutes without any MQTT message

---

## Settings Page

All cards start collapsed — tap a card header to expand it.

### Printer tabs (Printer 1 / Printer 2)
- Printer name
- Connection mode: LAN or Cloud
- Serial number
- LAN: IP address and access code
- Cloud: region (US/CN), access token (user ID derived automatically from JWT)
- **Bambu Cloud token fetch** — log in with email/password directly from settings; supports 2FA/MFA code entry
- **Token expiry indicator** — colour-coded expiry date shown after token is set; orange/red warning when < 30/7 days
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

### Weather
- **Location** — postal code or city name entered via on-screen QWERTY keyboard (no physical keyboard needed)
- **Units** — Celsius or Fahrenheit
- Weather shown on the idle clock screen when no prints are active

### Print History
- Completed prints logged automatically (job name, printer, layer count, duration, timestamp)
- History viewable in Settings; can be cleared

### System
- **Timezone** — select from common timezones; DST handled automatically by the system
- **Reboot** — reboots the Surface RT (with confirmation)
- **Shutdown** — shuts down the Surface RT (with confirmation)
- **Terminal** — opens an xterm window on the display

### Access & Security
- **PIN protection** — set a numeric PIN; entered via on-screen numpad (no keyboard needed)
- **LAN access toggle** — enable/disable access from other devices on the network
- **Local PIN toggle** — optionally require PIN for settings access even from the kiosk itself

### About
- Project description and links
- **Version info** — shows installed version and latest available version from GitHub
- **Check for Updates** — compares local version.txt against GitHub
- **Update Now** — one-tap OTA update with full-screen "Updating" overlay; auto-reloads dashboard after server restarts
- **Deep-linked from dashboard** — "Update Now" banner on the dashboard links directly to `/settings#about`, auto-expanding and scrolling to the About card

### Theme
- **Instant theme application** — saved theme colors are injected server-side into the HTML `<head>` before the browser paints, eliminating the flash of default theme on page load
- **6 theme presets** — Default, Bambu, Mono Green, Neon, Warm, Ocean
- Theme applies consistently across dashboard and settings pages

---

## AMS Support

AMS tray data is parsed from the Bambu Cloud MQTT stream and displayed as a color strip at
the bottom of each printer panel.

- **Filament colors** — actual spool colors from AMS RFID tags
- **Remaining %** — percentage of filament remaining per slot (when reported by AMS)
- **Active tray** — highlighted with accent color and `▶` when state=27 (actively feeding)
- **In-job trays** — subtly outlined when part of the current print job; uses `mapping` field for reliability in cloud mode
- **Empty slots** — faded to indicate no filament loaded; detects states 10, 24, and slots with no filament type
- **Multiple AMS units** — supports up to 2 AMS units (8 trays) per printer

> **Note:** In cloud MQTT mode, `nozzle_temper` reports only the **inactive** nozzle on dual-nozzle printers.
> True L/R temps come from the `extruder` object, which is nested inside the `device` sub-object of the MQTT
> payload (not inside `print`). The server uses a recursive search to locate it regardless of nesting depth.
> For single-nozzle printers, `nozzle_temper` is used normally. In-job tray detection uses the `mapping` field
> rather than the stale `tray_now` field for accuracy during cloud prints.

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
  "bambu_token": "YOUR_TOKEN",
  "enabled": true
}
```

> `bambu_user_id` is no longer required — the user ID is derived automatically from the JWT token.

**Getting your token (easiest):**
Use Settings → Printer → Fetch Token. Enter your Bambu Lab email and password; 2FA/MFA is supported.

**Getting your token manually:**
1. Log into [bambulab.com](https://bambulab.com) in your browser
2. Open Developer Tools (F12) → Application → Cookies → bambulab.com
3. Copy the `token` cookie value as `bambu_token`

> Tokens expire every ~3 months. A warning banner appears on the dashboard when a token is within 30 days of expiry. When a printer shows offline and logs show "Bad credentials", fetch a fresh token in Settings.

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

The web server binds to `0.0.0.0` (all interfaces) to support optional LAN access.

- **LAN access** is disabled by default — enable it in Settings → Access if you want to view the dashboard from another device on your network
- **PIN protection** — set a PIN in Settings → Access to require authentication before any LAN visitor (or optionally the local kiosk user) can reach the settings page
- **Local PIN** — optional toggle to also require PIN for settings access from the kiosk itself
- All non-GET API routes are PIN-gated when PIN protection is active

---

## Troubleshooting

| Problem | Solution |
|---|---|
| Service won't start (KeyError: 'id') | Config corrupted — server auto-restores from known-good backup on next restart |
| Cloud printer offline | Token expired — use Settings → Printer → Fetch Token, or get fresh token from bambulab.com cookies |
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

### v1.7.12 — April 2026
- **Larger 5-day forecast cards** — wider cards, 72×72 colour icons (was 48×48), and bigger day-name / hi-lo / description text for readability across the kiosk.

### v1.7.11 — April 2026
- **Hotfix for v1.7.10 dashboard** — the previous release shipped with a corrupted `updateWeather()` JavaScript block that threw a syntax error on page load, leaving the dashboard blank below the header (no printers, no idle clock). Function rewritten cleanly.

### v1.7.10 — April 2026
- **Touch-friendly display-timeout control** — the tiny number-input arrows on the Settings page have been replaced with a large `−` / value / `+` stepper. Tapping the value opens the existing on-screen keyboard in numeric mode so a full number can be typed.
- **Generalised on-screen keyboard** — `openKeyboard(targetId, {numMode, onSave})` can now be wired to any input, not only the weather-location field.
- **Bigger, colour weather on the dashboard** — current-condition row now shows a Twemoji colour icon plus larger, higher-contrast temperature/description text. The 5-day forecast cards are larger with colour Twemoji icons too.

### v1.7.9 — April 2026
- **Allow tap-to-wake without PIN** — added `/api/display/wake` to `_OPEN_PATHS` so it is reachable even when LAN PIN protection or local PIN is enabled. Waking the screen is harmless and gating it defeated the v1.7.8 feature on PIN-protected devices.

### v1.7.8 — April 2026
- **Tap-to-wake** — touching the screen (or pressing any key) now wakes the backlight and resets the display idle timer for one full timeout window, so the user can read the idle clock / weather without having to wait for a print event. Implemented as a new `/api/display/wake` endpoint plus a throttled (5 s) `touchstart` / `mousedown` / `keydown` listener on both the dashboard and settings pages. `display_monitor` honours `_user_activity_ts` the same way it honours an active print.

### v1.7.7 — April 2026
- **Better update UX** — the Settings page now shows the same full-screen “Updating” overlay with spinner that the dashboard uses, instead of a tiny status line. Overlay text progresses through `Downloading update…` → `Restarting server…` → `Reloading…` so the user can see what's happening.
- **Post-reload success toast** — after the page comes back, a green toast confirms the result: `Updated to v1.7.7 (was v1.7.6)` or `Reinstalled v1.7.7`. The previous version is stashed in `sessionStorage` before the request fires.

### v1.7.6 — April 2026
- **Fix dashboard not auto-reloading after OTA update** — the post-update poll reloaded as soon as `/api/version` responded, but the server stays alive for ~5–10 s while `bambu-update` downloads files, so the poll succeeded against the *old* server and reloaded the page before the actual restart, leaving the dashboard showing the previous version. Reworked both `dashboard.html` and `settings.html` polls to first wait for the server to go DOWN (proves the restart fired), then wait for it to come back UP, with a 120 s safety timeout.
- **Add “Reinstall” button** to Settings → About — always visible (independent of whether a newer version is available); re-downloads and reinstalls the current version through the same OTA path so the update flow can be tested end-to-end without a real version bump.

### v1.7.5 — April 2026
- **Fix `bambu-update` placeholder URL** — the updater script shipped with `YOUR_GITHUB_USERNAME` placeholders that were never substituted, so every OTA update from the dashboard “Update Now” button silently failed with `Failed to download bambu_server.py — check repo URL`. The `--check` mode also reported `Latest version: unknown`. Replaced both URLs with the real `rbjones21` repo. **In-UI updates now work**.

### v1.7.4 — April 2026
- **Fix kiosk XAUTHORITY path** — `bambuhelper-kiosk.service` was pointing `XAUTHORITY` at `/root/.Xauthority`, which doesn't exist on a LightDM system. Chromium then failed with `Authorization required, but no authorization protocol specified` / `Missing X server or $DISPLAY` and exited 1. Updated to `/var/run/lightdm/root/:0`, matching the path Xorg is actually launched with (`Xorg :0 -seat seat0 -auth /var/run/lightdm/root/:0`). Same fix that was previously applied to the Python display monitor in v1.6.4.

### v1.7.3 — April 2026
- **Fix kiosk crash-loop after Chromium 147 upgrade** — Chromium 147 refuses to run as root without `--no-sandbox` (`Running as root without --no-sandbox is not supported`), causing `bambuhelper-kiosk.service` to exit 1 on every restart. Added `--no-sandbox` to the kiosk launch command. The kiosk already runs as root in a single-user appliance context, so the sandbox provides no meaningful security boundary here.

### v1.7.2 — April 2026
- **Fix kiosk failing to start after v1.7.1** — `bambuhelper-noblank.service` was claiming `/dev/tty1` (`StandardInput=tty` + `TTYPath=`), which raced with LightDM/the kiosk for ownership of tty1 and prevented the dashboard browser from launching on boot. The service now redirects setterm output to `/dev/tty1` without owning the TTY.

### v1.7.1 — April 2026
- **Sleep/wake hardening (installer)** — `install.sh` now masks `sleep.target`, `suspend.target`, `hibernate.target`, and `hybrid-sleep.target`; drops a `logind.conf.d` snippet that ignores lid switch, power key, and idle action; installs a `bambuhelper-noblank.service` oneshot that runs `setterm --blank 0 --powerdown 0 --powersave off` against `/dev/tty1` to disable the kernel framebuffer console blanker; and adds a udev rule pinning `power/control=on` for `backlight`, `drm`, and `graphics` devices so the kernel never autosuspends the display controller. Re-run `sudo bash install.sh` after updating to apply these.
- **Display watchdog logging** — `wake_screen()` now records a timestamp on every call (success or failure) and logs failures at warning level instead of debug. `display_monitor()` emits a heartbeat every 5 minutes showing screen state, time since last backlight write, time since last wake call, and last wake reason — so the next time the device hangs, `journalctl -u bambuhelper` will show whether the dashboard tried to wake it (kernel-side hang) or never tried (logic bug).

### v1.7.0 — April 2026
- **DPMS removed entirely** — Tegra 3 display controller hangs on DPMS resume and forces a hard reset. `wake_screen()` and `screen_off()` now use the backlight (`/sys/class/backlight/backlight/brightness`) only. Display monitor startup runs `xset -dpms` and `xset s off` to prevent X from ever triggering DPMS automatically.

### v1.6.4 — April 2026
- **XAUTHORITY auto-discovery** — auto-detect the LightDM root cookie at `/var/run/lightdm/root/:0` instead of relying on `~/.Xauthority`, so display control commands actually run as the right user. Path is logged at startup.

### v1.6.3 — April 2026
- **Instant screen wake on print events** — `wake_screen()` extracted to a module-level helper callable from any thread; the MQTT handler now wakes the display the moment a print transitions to RUNNING or a new error appears. Display monitor poll reduced from 30 s to 10 s as a fallback.
- **H2D chamber temp / filament type / chamber theme color** fixes.
- **5-day forecast** added to the idle clock screen, plus security hardening on input validation.

### v1.6.2 / v1.6.1 — April 2026
- **Display timeout via backlight** — switched screen on/off from `xset dpms` to writing the backlight brightness directly. DPMS proved unreliable on the Surface RT.
- Fixed `DISPLAY_ENV` clobbering the entire process environment (PATH was being lost). Added transition logging for display on/off events.
- Misc HMS dismiss persistence and lookup-table fixes.

### v1.6.0 — April 2026
- **Update notification bar** — blue banner on the dashboard when a new version is available; links directly to Settings → About for one-tap update
- **Full-screen update overlay** — "Updating..." spinner shown during OTA update; dashboard auto-reloads after server restart
- **HMS error descriptions** — local fallback table with 60+ common codes; cloud API enrichment with caching; HMS codes shown with human-readable descriptions on the dashboard
- **HMS cache fix** — empty cloud API results no longer cached permanently, allowing fallback table entries to work
- **HMS dismiss fix** — dismissed codes persist across MQTT heartbeats; new codes no longer reset the entire dismissed list
- **Nozzle clumping code** — added `0C00-0300-0002-001C` (AI detected nozzle clumping) to fallback table
- **Front door sensor codes** — added `0300-9600-0003-0001/0002` (front door open) to fallback table
- **Battery/charging indicator** — ⚡ bolt icon when charging; "Full" text when plugged in at 95%+
- **False offline fix** — `last_update` now set on all MQTT messages (not just print); staleness timeout increased to 120s; 8-second dashboard grace period prevents false "Offline" flashes
- **Display timeout fix** — `show_clock` setting now respected: unchecked = screen off immediately after prints finish; checked = idle clock shown for timeout period then screen off
- **Duplicate print history fix** — in-memory guard + 10-minute dedup window prevents repeated FINISH messages from creating duplicate history entries
- **Theme flash eliminated** — saved theme colors injected server-side into HTML `<head>` before paint; no more flash of default dark-blue theme on page load
- **Settings deep-link** — `/settings#about` auto-expands and scrolls to the About card

### v1.5.x — March–April 2026 (incremental)
- v1.5.1: README version bump + finish recording guard
- v1.5.2: Print history deduplication (10-minute window)
- v1.5.3: Display timeout respects show_clock setting
- v1.5.4: Full-screen update overlay + auto-reload
- v1.5.5: False offline flash fix (3-layer approach)
- v1.5.6: Battery charging indicator (bolt + Full text)
- v1.5.7: Update available notification bar on dashboard
- v1.5.8: HMS fallback table for 0300-9600 door sensor codes
- v1.5.9: HMS cache bug fix (empty results no longer cached)
- v1.5.10: HMS description correction (door sensor)
- v1.5.11: Nozzle clumping fallback + settings deep-link
- v1.5.12: HMS dismiss persistence fix
- v1.5.13: Theme flash elimination (server-side color injection)

### v1.4.0 — March 2026
- **PIN protection** — numeric PIN with on-screen numpad (no physical keyboard required); gates LAN access and optionally the local settings page
- **LAN access control** — toggle remote access on/off from Settings → Access; disabled by default
- **Local PIN toggle** — optionally require PIN for settings access from the kiosk itself
- **Bambu Cloud token fetch** — log in with email/password directly from settings; 2FA/MFA code entry supported; user ID derived automatically from JWT (no manual entry)
- **Token expiry warning** — dashboard shows orange/red banner when any cloud token is within 30/7 days of expiry; expiry date shown in settings after token is set
- **Weather on idle clock** — current conditions, temperature, and high/low shown on the idle clock screen; location set via on-screen QWERTY keyboard in settings
- **Print history** — completed prints logged automatically (name, printer, layers, duration, date); viewable and clearable in Settings → Print History
- **Dual nozzle display** — L and R temp bars shown for H2D and H2C; active nozzle highlighted; parses `extruder` object from `device` sub-object in cloud MQTT (packed 32-bit temps: low16 = actual, high16 = target); `nozzle_temper` skipped for dual-nozzle printers (it reports the inactive nozzle only)
- **AMS empty slot fix** — correctly handles state 10, state 24, and untyped slots as empty; fixes false-loaded display on AMS 2 slot 4
- **Active filament fix** — prefers `in_job` flag (from `mapping` field) over stale `tray_now` for active tray highlighting during cloud prints
- **HMS errors clear on RUNNING** — transient HMS codes at print start are cleared when the print resumes/begins, avoiding spurious error overlays
- **Old Chromium compatibility** — removed emoji characters (💧🔥📧🔢) that don't render on Surface RT's Chromium build; replaced with text equivalents

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
