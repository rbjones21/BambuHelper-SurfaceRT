# BambuHelper Surface RT — v2 (LAN + Cloud Mode)

Monitors two Bambu Lab printers (H2D + H2C) on a Microsoft Surface RT running Debian 12.
Supports both **LAN mode** (direct local connection) and **Bambu Cloud mode** per printer —
you can even mix modes (one printer on LAN, one via cloud).

---

## Connection Modes

### LAN Mode (recommended)
Connects directly to the printer over your local network using MQTT over TLS.
- Requires **Developer Mode** enabled on the printer
- Faster updates, no internet dependency
- Printer and Surface RT must be on the same network

### Cloud Mode
Connects via Bambu Lab's cloud MQTT broker (`us.mqtt.bambulab.com`).
- Does **not** require Developer Mode
- Works even when the printer is on a different network
- Requires a Bambu account token (see setup below)
- Slightly slower updates (routed through Bambu's servers)

---

## Installation

### 1. Transfer files to the Surface RT

```bash
scp -r bambuhelper-surface-v2/ root@<SURFACE_RT_IP>:/tmp/
```

Or copy to a USB drive and transfer manually.

### 2. Run the installer

```bash
cd /tmp/bambuhelper-surface-v2
sudo bash install.sh
```

### 3. Configure your printers

```bash
nano /etc/bambuhelper/config.json
```

---

## Config Reference

### LAN Mode printer

```json
{
  "id": "printer1",
  "name": "H2D",
  "mode": "lan",
  "ip": "192.168.1.100",
  "serial": "YOUR_SERIAL",
  "access_code": "YOUR_8_CHAR_CODE",
  "enabled": true
}
```

**Finding LAN credentials on the printer touchscreen:**
- IP address:    Settings → Network → IP Address
- Serial number: Settings → Device → Serial Number
- Access code:   Settings → Network → LAN Access Code
  *(Enable Developer Mode first: Settings → General → Developer Mode)*

---

### Cloud Mode printer

```json
{
  "id": "printer2",
  "name": "H2C",
  "mode": "cloud",
  "region": "us",
  "serial": "YOUR_SERIAL",
  "bambu_user_id": "123456789",
  "bambu_token": "eyJ...(long token string)...",
  "enabled": true
}
```

**Region options:** `"us"` (default), `"cn"` (China accounts)

#### How to get your Bambu User ID and Token

1. Open [MakerWorld](https://makerworld.com) in your browser and log in
2. Open your browser's **Developer Tools** (F12)
3. Go to **Application → Cookies → https://makerworld.com**
4. Find the cookie named **`token`** — copy its full value as `bambu_token`
5. Go to the **Network** tab, reload the page, find a request to `makerworld.com`
6. Click **my/preference** in the request list, look in the response JSON for `uid`
   — copy this number as `bambu_user_id`

Alternatively, visit this URL while logged into MakerWorld and find `uid` in the JSON:
```
https://makerworld.com/api/v1/design-user-service/my/preference
```

> **Note:** Cloud tokens expire periodically. If the dashboard shows a printer
> offline and the logs show "Bad credentials", you need to refresh your token
> using the steps above and update config.json.

---

## Mixed Mode Example

One printer on LAN, one via cloud — both on the same dashboard:

```json
{
  "printers": [
    {
      "id": "printer1",
      "name": "H2D",
      "mode": "lan",
      "ip": "192.168.1.100",
      "serial": "ABC123",
      "access_code": "12345678",
      "enabled": true
    },
    {
      "id": "printer2",
      "name": "H2C",
      "mode": "cloud",
      "region": "us",
      "serial": "DEF456",
      "bambu_user_id": "987654321",
      "bambu_token": "eyJhbGci...",
      "enabled": true
    }
  ]
}
```

---

## Useful Commands

```bash
# View live logs (very useful for debugging connection issues)
journalctl -u bambuhelper -f

# Restart after editing config.json
sudo systemctl restart bambuhelper

# Check status
systemctl status bambuhelper
systemctl status bambuhelper-kiosk

# Stop kiosk to access desktop
sudo systemctl stop bambuhelper-kiosk

# Temporarily disable a printer without editing JSON
# Set "enabled": false in config.json then restart
```

---

## Troubleshooting

| Problem | Solution |
|---|---|
| LAN printer offline | Confirm Developer Mode is on, check IP and access code |
| Cloud printer offline | Token may have expired — get a fresh token from MakerWorld |
| "Bad credentials" in logs | Wrong access_code (LAN) or bambu_token (cloud) |
| "Not authorised" in logs | Wrong bambu_user_id — check the `uid` field carefully |
| Dashboard not loading | Check `journalctl -u bambuhelper -f` for Python errors |
| No data after connecting | Try restarting the service — it sends a pushall request on connect |

---

## Project Structure

```
/opt/bambuhelper/
├── bambu_server.py       MQTT bridge + Flask server (LAN + cloud)
├── templates/
│   └── dashboard.html    Touch dashboard UI
└── venv/                 Python virtual environment

/etc/bambuhelper/
└── config.json           Printer config (edit this)
```

---
*BambuHelper Surface RT v2 — March 2026*
