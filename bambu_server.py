#!/usr/bin/env python3
"""
BambuHelper Surface RT - MQTT bridge + web dashboard server
Supports both LAN mode (direct printer connection) and
Bambu Cloud mode (via us.mqtt.bambulab.com) per printer.
"""

import json
import os
import re
import base64
import shutil
import ssl
import subprocess
import threading
import time
import logging
import urllib.request
import urllib.error
import io
import secrets
from flask import Flask, render_template, jsonify, request, Response, session, redirect, send_file
from flask_socketio import SocketIO
import paho.mqtt.client as mqtt

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config paths
# ---------------------------------------------------------------------------
CONFIG_PATH            = '/etc/bambuhelper/config.json'
KNOWN_GOOD_CONFIG_PATH = '/etc/bambuhelper/config.known-good.json'
DISMISSED_HMS_PATH     = '/etc/bambuhelper/dismissed_hms.json'
PRINT_HISTORY_PATH     = '/etc/bambuhelper/print_history.json'

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------
def validate_and_repair_config(config):
    """Ensure config has required fields, repair in place if missing."""
    if not isinstance(config, dict):
        return False
    if 'printers' not in config or not isinstance(config['printers'], list):
        return False
    for i, cfg in enumerate(config['printers']):
        if not isinstance(cfg, dict):
            config['printers'][i] = {"id": f"printer{i+1}", "enabled": False}
            continue
        if "id" not in cfg or not cfg["id"]:
            cfg["id"] = f"printer{i+1}"
        cfg.setdefault("name",    cfg["id"])
        cfg.setdefault("mode",    "cloud")
        cfg.setdefault("enabled", True)
        cfg.setdefault("serial",  "")
    return True

def save_config_to_disk(config):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, 'w') as f:
        json.dump(config, f, indent=2)

def load_config():
    for path in [CONFIG_PATH, KNOWN_GOOD_CONFIG_PATH]:
        try:
            with open(path, 'r') as f:
                config = json.load(f)
            if validate_and_repair_config(config):
                if path == KNOWN_GOOD_CONFIG_PATH:
                    log.warning("Main config invalid — restored from known-good backup")
                    save_config_to_disk(config)
                return config
            log.warning(f"Config at {path} failed validation, trying next...")
        except FileNotFoundError:
            log.warning(f"Config not found at {path}")
        except json.JSONDecodeError as e:
            log.warning(f"Config at {path} is invalid JSON: {e}")
    log.warning("All configs invalid or missing, using defaults")
    return {
        "printers": [
            {"id": "printer1", "name": "Printer 1", "mode": "cloud", "enabled": False},
            {"id": "printer2", "name": "Printer 2", "mode": "cloud", "enabled": False}
        ]
    }

CONFIG = load_config()

# Ensure a stable secret key exists (generated once, stored in config)
if 'secret_key' not in CONFIG:
    CONFIG['secret_key'] = secrets.token_hex(32)
    save_config_to_disk(CONFIG)

# ---------------------------------------------------------------------------
# Access control helpers — LAN access + settings PIN
# ---------------------------------------------------------------------------
def is_local_request():
    return request.remote_addr in ('127.0.0.1', '::1', 'localhost')

def lan_access_enabled():
    return bool(CONFIG.get('lan_access', False))

def settings_pin():
    return str(CONFIG.get('settings_pin', ''))

def pin_protect_local():
    # Default True: PIN required even from kiosk/local browser
    return bool(CONFIG.get('pin_protect_local', True))

def pin_authenticated():
    return session.get('pin_authenticated') is True

PIN_PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BambuHelper — Enter PIN</title>
<style>
 body{margin:0;background:#0d1117;display:flex;align-items:center;justify-content:center;min-height:100vh;font-family:sans-serif;}
 .box{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:28px 24px;text-align:center;width:280px;}
 h2{color:#58a6ff;margin:0 0 6px;font-size:18px;}
 p{color:#8b949e;font-size:13px;margin:0 0 16px;}
 .disp{background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#e6edf3;
       font-size:28px;letter-spacing:10px;text-align:center;padding:10px 12px;margin-bottom:16px;min-height:48px;}
 .grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:8px;}
 .k{background:#21262d;border:1px solid #30363d;border-radius:8px;color:#e6edf3;
    font-size:22px;padding:14px 0;cursor:pointer;touch-action:manipulation;}
 .k:active{background:#30363d;}
 .k.del{color:#f85149;}
 .k.ok{background:#238636;border-color:#238636;color:#fff;font-size:18px;}
 .k.ok:active{background:#2ea043;}
 .err{color:#f85149;font-size:12px;margin-top:10px;display:none;}
</style></head><body>
<div class="box">
  <h2>BambuHelper</h2><p>Enter PIN to continue</p>
  <div class="disp" id="disp">&#8203;</div>
  <div class="grid">
    <button class="k" onclick="key('1')">1</button>
    <button class="k" onclick="key('2')">2</button>
    <button class="k" onclick="key('3')">3</button>
    <button class="k" onclick="key('4')">4</button>
    <button class="k" onclick="key('5')">5</button>
    <button class="k" onclick="key('6')">6</button>
    <button class="k" onclick="key('7')">7</button>
    <button class="k" onclick="key('8')">8</button>
    <button class="k" onclick="key('9')">9</button>
    <button class="k del" onclick="key('back')">&#9003;</button>
    <button class="k" onclick="key('0')">0</button>
    <button class="k ok" onclick="key('ok')">&#10003;</button>
  </div>
  <div class="err" id="err">Incorrect PIN</div>
</div>
<script>
var v='';
function key(k){
  if(k==='back'){v=v.slice(0,-1);}
  else if(k==='ok'){if(v.length>=4)doAuth();return;}
  else{if(v.length<8)v+=k;}
  document.getElementById('disp').textContent='\u25cf'.repeat(v.length)||'\u200b';
}
async function doAuth(){
  var r=await fetch('/api/auth/pin',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({pin:v})});
  var d=await r.json();
  if(d.ok){location.href='{next}';}
  else{document.getElementById('err').style.display='block';v='';document.getElementById('disp').textContent='\u200b';}
}
</script></body></html>"""

LAN_BLOCKED_PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>BambuHelper — Access Restricted</title>
<style>body{margin:0;background:#0d1117;display:flex;align-items:center;justify-content:center;
 min-height:100vh;font-family:sans-serif;}
 .box{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:32px 28px;text-align:center;width:320px;}
 h2{color:#f85149;margin:0 0 8px;} p{color:#8b949e;font-size:13px;}</style></head>
<body><div class="box"><h2>Access Restricted</h2>
<p>LAN access is not enabled on this device.<br>Enable it in Settings → System on the local display.</p>
</div></body></html>"""

# ---------------------------------------------------------------------------
# Dismissed HMS persistence — survives reboots
# ---------------------------------------------------------------------------
def load_dismissed_hms():
    """Return {printer_id: [code, ...]} from disk, or empty dict."""
    try:
        with open(DISMISSED_HMS_PATH, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_dismissed_hms(dismissed):
    """Persist {printer_id: [code, ...]} to disk."""
    try:
        os.makedirs(os.path.dirname(DISMISSED_HMS_PATH), exist_ok=True)
        with open(DISMISSED_HMS_PATH, 'w') as f:
            json.dump(dismissed, f)
    except Exception as e:
        log.warning(f"Could not save dismissed HMS: {e}")

_dismissed_hms_store = load_dismissed_hms()

# ---------------------------------------------------------------------------
# HMS description cache + cloud lookup
# ---------------------------------------------------------------------------
# Small local fallback for the most common codes — avoids a network call for
# the codes users encounter most often.  Keys use the canonical XXXX-XXXX-XXXX-XXXX
# format (upper-case).  AMS slot variants (0700/0701/… and 1800/…) share a
# common entry because the prefix varies per slot but the error meaning is the same.
_HMS_FALLBACK = {
    # Filament / AMS
    "0700-2000-0002-0001": "AMS filament ran out",
    "0700-2000-0002-0002": "AMS slot is empty",
    "0700-2000-0002-0003": "AMS filament may be broken inside AMS",
    "0700-2000-0002-0004": "AMS filament may be broken in toolhead",
    "0700-2000-0002-0005": "AMS filament ran out — purge abnormal",
    "0700-2000-0002-0006": "AMS switch failed — filament not detected after switch",
    "0700-7000-0002-0007": "AMS filament ran out — insert new filament and retry",
    "0700-7000-0002-0001": "Failed to pull filament from extruder — check for clog",
    "0700-7000-0002-0002": "Failed to feed filament into toolhead — check if stuck",
    "0700-7000-0002-0003": "Failed to extrude filament — check extruder/nozzle clog",
    "0700-7000-0002-0004": "Failed to pull filament back to AMS — check if stuck",
    "0700-7000-0002-0006": "Timeout purging old filament — check clog",
    "07FF-2000-0002-0001": "External filament ran out — load new filament",
    "07FF-2000-0002-0002": "External filament missing — load new filament",
    # Nozzle / hotend
    "0300-0200-0001-0001": "Nozzle temperature abnormal — heater may be short circuit",
    "0300-0200-0001-0002": "Nozzle temperature abnormal — heater may be open circuit",
    "0300-0200-0001-0003": "Nozzle temperature abnormal — heater over temperature",
    "0300-0200-0001-0006": "Nozzle temperature abnormal — sensor may be short circuit",
    "0300-0200-0001-0007": "Nozzle temperature abnormal — sensor may be open circuit",
    "0300-0200-0001-0009": "Nozzle temperature abnormal — hotend may not be installed",
    # Heatbed
    "0300-0100-0001-0003": "Heatbed temperature abnormal — heater over temperature",
    "0300-0100-0001-0006": "Heatbed temperature abnormal — sensor short circuit",
    "0300-0100-0001-0007": "Heatbed temperature abnormal — sensor open circuit",
    "0300-0100-0001-0008": "Heatbed heating abnormal — heating modules may be broken",
    "0300-0100-0001-000A": "Heatbed temperature abnormal — AC board may be broken",
    # Bed leveling / Z
    "0300-0D00-0002-0001": "Heatbed homing abnormal — check for nozzle residue",
    "0300-0D00-0001-0003": "Build plate not placed properly",
    "0300-0D00-0001-000B": "Z axis stuck — check Z sliders for foreign matter",
    # Motors
    "0300-0600-0001-0001": "Motor-A open circuit — check connector or motor",
    "0300-0600-0001-0002": "Motor-A short circuit — motor may have failed",
    "0300-0700-0001-0001": "Motor-B open circuit — check connector or motor",
    "0300-0800-0001-0001": "Motor-Z open circuit — check connector or motor",
    # Fans
    "0300-0300-0001-0001": "Hotend cooling fan stopped — may be stuck or disconnected",
    "0300-0300-0002-0002": "Hotend fan speed slow",
    "0300-0400-0002-0001": "Part cooling fan too slow or stopped",
    # Extruder
    "0300-1A00-0002-0001": "Nozzle wrapped in filament or build plate placed incorrectly",
    "0300-1A00-0002-0002": "Nozzle clog detected by extrusion force sensor",
    # Chamber
    "0300-9000-0001-0001": "Chamber heating failed — heater may not be blowing hot air",
    "0300-9000-0001-0002": "Chamber heating failed — may not be enclosed or ambient too cold",
    "0300-9400-0003-0001": "Chamber cooling too slow — open cover to assist cooling",
    # System / AP
    "0500-0300-0001-0001": "MC module malfunctioning — restart device",
    "0500-0300-0001-0002": "Toolhead malfunctioning — restart device",
    "0500-0300-0001-0003": "AMS module malfunctioning — restart device",
    "0500-0100-0003-0004": "SD card is full",
    "0500-0100-0003-0005": "SD card error",
    "0500-0200-0002-0001": "Failed to connect to internet — check network",
    "0500-0400-0001-0001": "Failed to download print job — check network",
    # Camera / Live View
    "0500-0600-0002-0001": "Toolhead camera not connected — check hardware connection",
    "0500-0600-0002-0002": "Nozzle camera not connected — check hardware connection",
    "0500-0600-0002-0004": "Live View camera not connected — check hardware connection",
    "0500-0600-0002-0031": "Toolhead camera not connected — check cable connections",
    "0500-0600-0002-0032": "Nozzle camera not connected — check cable connections",
    "0500-0600-0002-0034": "Live View camera not connected — check cable connections",
    "0500-0600-0002-0044": "Live View camera not connected — check cable connections",
    # Build plate / print file
    "0500-8051-0001-0001": "Build plate type does not match the Gcode file — adjust slicer settings or use correct plate",
    "0500-8051-0003-3C5E": "Build plate type does not match the Gcode file — adjust slicer settings or use correct plate",
    # Lidar / AI
    "0C00-0100-0001-0004": "Micro Lidar lens dirty",
    "0C00-0300-0003-0006": "Purged filament piled up in waste chute",
    "0C00-0300-0003-0008": "Possible spaghetti defects detected",
    "0C00-0300-0002-000C": "Build plate marker not detected",
    # Power
    "0300-4100-0001-0001": "System voltage unstable — power failure protection triggered",
}

# In-memory cache: code_str (XXXX-XXXX-XXXX-XXXX upper) → description string or None
_hms_desc_cache: dict = {}
_hms_desc_lock  = threading.Lock()

def _normalise_hms_code(code: str) -> str:
    """Return upper-case code with dashes: 0700-2000-0002-0001."""
    return code.upper().replace('_', '-')

def _ams_generic_key(code: str) -> str:
    """
    AMS error codes share the same meaning across slots/units.
    Map common AMS prefixes to the canonical '0700' variant so the
    fallback table gets a hit without needing 200+ entries.
    AMS unit prefixes: 0700-0707, 1800-1807, 0580-0587
    AMS-lite prefixes: 1200-1203
    """
    ams_prefixes = {
        '0701','0702','0703','0704','0705','0706','0707',
        '1800','1801','1802','1803','1804','1805','1806','1807',
        '0580','0581','0582','0583','0584','0585','0586','0587',
        '1201','1202','1203',
    }
    parts = code.split('-')
    if len(parts) == 4 and parts[0] in ams_prefixes:
        return '0700-' + '-'.join(parts[1:])
    return code

def lookup_hms_description(code: str) -> str:
    """Return a human-readable description for an HMS code.

    Strategy:
    1. Check in-memory cache (hit → instant return).
    2. Check local fallback table (covers the most common codes, including
       AMS slot generalisation).
    3. Query Bambu's cloud HMS API.  Result is cached regardless.
    Falls back to empty string on any error — never raises.
    """
    norm    = _normalise_hms_code(code)
    generic = _normalise_hms_code(_ams_generic_key(norm))

    with _hms_desc_lock:
        if norm in _hms_desc_cache:
            return _hms_desc_cache[norm]

    # Local fallback
    desc = _HMS_FALLBACK.get(norm) or _HMS_FALLBACK.get(generic) or ''

    if not desc:
        # Cloud lookup — format the code as expected by Bambu's query API
        # The API expects the code WITHOUT dashes, 16 hex chars
        api_code = norm.replace('-', '_')  # e.g. 0700_2000_0002_0001
        try:
            url = f"https://e.bambulab.com/query.php?lang=en&e={api_code}"
            req = urllib.request.Request(url, headers={"User-Agent": "BambuHelper/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = resp.read().decode('utf-8', errors='replace')
            data = json.loads(body)
            # Response shape: {"result":0, "data":{"device_hms":{"en":[{"ecode":"…","intro":"…"}]}}}
            device_hms = (data.get('data') or {}).get('device_hms') or {}
            entries    = device_hms.get('en') or []
            if not entries:
                # Some responses have device_error instead
                device_err = (data.get('data') or {}).get('device_error') or {}
                entries    = device_err.get('en') or []
            if entries and isinstance(entries, list):
                intro = entries[0].get('intro') or ''
                desc  = intro.strip()
        except Exception as e:
            log.debug(f"HMS cloud lookup failed for {norm}: {e}")

    with _hms_desc_lock:
        _hms_desc_cache[norm] = desc
    return desc

def _enrich_hms_codes(codes: list) -> list:
    """Convert a list of code strings into [{"code": …, "desc": …}] dicts."""
    result = []
    for c in codes:
        desc = lookup_hms_description(c)
        result.append({"code": c, "desc": desc})
    return result

# ---------------------------------------------------------------------------
# Cloud broker config
# ---------------------------------------------------------------------------
CLOUD_BROKERS = {
    "us": "us.mqtt.bambulab.com",
    "cn": "cn.mqtt.bambulab.com",
    "eu": "us.mqtt.bambulab.com",
}

def _decode_jwt_payload(token):
    """Return the decoded JWT payload dict, or {} on failure."""
    try:
        payload_b64 = token.split('.')[1]
        payload_b64 += '=' * (4 - len(payload_b64) % 4)
        return json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        return {}

def get_user_id_from_token(token):
    """Extract Bambu numeric user ID from the JWT access token payload."""
    payload = _decode_jwt_payload(token)
    return str(payload.get('username') or payload.get('sub') or payload.get('user_id', ''))

def get_token_expiry(token):
    """Return Unix timestamp of token expiry, or None."""
    exp = _decode_jwt_payload(token).get('exp')
    return int(exp) if exp else None

def get_connection_params(printer_cfg):
    mode = printer_cfg.get("mode", "lan").lower()
    if mode == "cloud":
        region   = printer_cfg.get("region", "us")
        host     = CLOUD_BROKERS.get(region, CLOUD_BROKERS["us"])
        token    = printer_cfg.get("bambu_token", "")
        # Prefer explicitly stored user_id; fall back to JWT-derived value
        user_id  = printer_cfg.get("bambu_user_id") or get_user_id_from_token(token)
        user     = f"u_{user_id}"
        pwd      = token
    else:
        host = printer_cfg["ip"]
        user = "bblp"
        pwd  = printer_cfg["access_code"]
    return host, 8883, user, pwd

# ---------------------------------------------------------------------------
# Printer state
# ---------------------------------------------------------------------------
def default_state(printer_cfg):
    return {
        "id":             printer_cfg["id"],
        "name":           printer_cfg["name"],
        "mode":           printer_cfg.get("mode", "lan"),
        "connected":      False,
        "enabled":        printer_cfg.get("enabled", True),
        "printing":       False,
        "progress":       0,
        "nozzle_temp":          0.0,
        "nozzle_target":        0.0,
        "nozzle_temp_l":        None,  # Left nozzle temp from extruder (id=1)
        "nozzle_temp_r":        None,  # Right nozzle temp from extruder (id=0)
        "nozzle_target_l":      None,
        "nozzle_target_r":      None,
        "nozzle_active_side":   None,  # 'L' or 'R' from extruder.state
        "has_dual_nozzle":      False, # True when nozzle_type starts with HH (H2D)
        "bed_temp":       0.0,
        "bed_target":     0.0,
        "chamber_temp":   0.0,
        "fan_part":       0,
        "fan_aux":        0,
        "fan_chamber":    0,
        "layer_current":  0,
        "layer_total":    0,
        "time_remaining": 0,
        "print_name":     "",
        "gcode_state":    "IDLE",
        "errors":         [],
        "hms_errors":     [],
        "dismissed_hms":  [],  # codes dismissed by user — suppressed until new codes arrive
        "spd_lvl":        2,
        "stage":          "",
        "last_update":    0,
        "nozzle_type":    "",
        "nozzle_diameter": "",
        "vir_slots":      [],
        "ams_trays":      [],
        "ams_job_slots":  [],
        "ams_active_id":  None,  # last known active tray ID (persisted across payloads)
    }

state_lock      = threading.Lock()
active_clients  = {}  # printer_id -> active mqtt client
printer_states  = {cfg["id"]: default_state(cfg) for cfg in CONFIG["printers"]}
last_payloads      = {}  # printer_id -> last raw print payload (for debug)
last_rich_payloads = {}  # printer_id -> last payload that contained nozzle_temper (for debug)
last_ams_payloads  = {}  # printer_id -> last payload that contained ams data (for debug)

# Restore persisted dismissed HMS codes into initial state
for _pid, _codes in _dismissed_hms_store.items():
    if _pid in printer_states and _codes:
        printer_states[_pid]['dismissed_hms'] = _codes
        log.info(f"[{_pid}] Restored {len(_codes)} dismissed HMS code(s) from disk")

# ---------------------------------------------------------------------------
# Weather cache
# ---------------------------------------------------------------------------
_weather_cache = {"data": None, "fetched_at": 0}
_WEATHER_TTL   = 1800  # 30 minutes

def fetch_weather():
    """Fetch weather from wttr.in for the configured location. Returns dict or None."""
    location = CONFIG.get('weather_location', '').strip()
    if not location:
        return None
    try:
        url = f"https://wttr.in/{urllib.request.quote(location)}?format=j1"
        req = urllib.request.Request(url, headers={"User-Agent": "BambuHelperRT/1.4"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        use_f = CONFIG.get('weather_unit', 'F').upper() == 'F'
        cur   = data['current_condition'][0]
        today = data['weather'][0]
        return {
            "temp":      cur['temp_F'] if use_f else cur['temp_C'],
            "feels":     cur['FeelsLikeF'] if use_f else cur['FeelsLikeC'],
            "high":      today['maxtempF'] if use_f else today['maxtempC'],
            "low":       today['mintempF'] if use_f else today['mintempC'],
            "desc":      cur['weatherDesc'][0]['value'],
            "humidity":  cur['humidity'],
            "unit":      'F' if use_f else 'C',
            "location":  location,
        }
    except Exception as e:
        log.warning(f"Weather fetch failed: {e}")
        return None

def get_weather():
    """Return cached weather, refreshing if stale."""
    now = time.time()
    if now - _weather_cache['fetched_at'] > _WEATHER_TTL or _weather_cache['data'] is None:
        _weather_cache['data']       = fetch_weather()
        _weather_cache['fetched_at'] = now
    return _weather_cache['data']

# ---------------------------------------------------------------------------
# Print history
# ---------------------------------------------------------------------------
PRINT_HISTORY_MAX = 100

def load_print_history():
    try:
        with open(PRINT_HISTORY_PATH, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def save_print_history(history):
    try:
        os.makedirs(os.path.dirname(PRINT_HISTORY_PATH), exist_ok=True)
        with open(PRINT_HISTORY_PATH, 'w') as f:
            json.dump(history[-PRINT_HISTORY_MAX:], f)
    except Exception as e:
        log.warning(f"Could not save print history: {e}")

def record_print_finished(state):
    """Append a completed print record to history."""
    name = state.get('print_name', '').strip()
    if not name:
        return
    history = load_print_history()
    history.append({
        "printer":   state.get('name', state['id']),
        "job":       name,
        "finished":  int(time.time()),
        "layers":    state.get('layer_total', 0),
        "started":   state.get('print_start_time', None),
    })
    save_print_history(history)
    log.info(f"[{state['id']}] Print history recorded: {name!r}")

# ---------------------------------------------------------------------------
# Flask + SocketIO
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.config['SECRET_KEY'] = CONFIG['secret_key']
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# ---------------------------------------------------------------------------
# Access control — runs before every request
# ---------------------------------------------------------------------------
_OPEN_PATHS = {'/api/auth/pin', '/api/auth/logout'}

@app.before_request
def access_control():
    if request.path in _OPEN_PATHS:
        return  # always allow auth endpoints
    local = is_local_request()
    pin   = settings_pin()

    if not local:
        # Block LAN access if not enabled
        if not lan_access_enabled():
            return Response(LAN_BLOCKED_PAGE, status=403, mimetype='text/html')
        # PIN gates ALL routes from the network
        if pin and not pin_authenticated():
            if request.path.startswith('/api/'):
                return jsonify({"ok": False, "error": "PIN required"}), 401
            return Response(PIN_PAGE.replace('{next}', request.path), mimetype='text/html')
    else:
        # Local (kiosk): enforce PIN when pin_protect_local is enabled
        if pin and pin_protect_local() and not pin_authenticated():
            # Gate settings page and all state-changing API calls
            if request.path == '/settings':
                return Response(PIN_PAGE.replace('{next}', '/settings'), mimetype='text/html')
            if request.method != 'GET' and request.path.startswith('/api/'):
                return jsonify({"ok": False, "error": "PIN required"}), 401

# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------
@app.route('/')
def index():
    return render_template('dashboard.html')

@app.route('/settings')
def settings():
    return render_template('settings.html')

@app.route('/api/state')
def api_state():
    with state_lock:
        return jsonify([s for s in printer_states.values() if s.get('enabled', True)])

_VERSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'version.txt')
_REMOTE_VERSION_URL = 'https://raw.githubusercontent.com/rbjones21/BambuHelper-SurfaceRT/main/version.txt'

@app.route('/api/version')
def api_version():
    try:
        with open(_VERSION_FILE) as f:
            local = f.read().strip()
    except OSError:
        local = 'unknown'
    remote = None
    try:
        req = urllib.request.Request(_REMOTE_VERSION_URL, headers={'User-Agent': 'BambuHelper'})
        with urllib.request.urlopen(req, timeout=5) as r:
            remote = r.read().decode().strip()
    except Exception:
        pass
    return jsonify({'local': local, 'remote': remote})

@app.route('/api/update', methods=['POST'])
def api_update():
    def _do_update():
        time.sleep(1)
        try:
            subprocess.run(['/usr/local/bin/bambu-update', '--force'], check=False)
        except Exception as exc:
            log.error('Update failed: %s', exc)
    threading.Thread(target=_do_update, daemon=True).start()
    return jsonify({'ok': True, 'message': 'Update started. Server will restart shortly.'})

@app.route('/api/token_expiry')
def api_token_expiry():
    """Return token expiry info for all cloud-mode printers."""
    result = []
    now = int(time.time())
    for cfg in CONFIG.get('printers', []):
        if cfg.get('mode', 'lan') == 'cloud' and cfg.get('bambu_token'):
            exp = get_token_expiry(cfg['bambu_token'])
            days_left = int((exp - now) / 86400) if exp else None
            result.append({
                'id':        cfg['id'],
                'name':      cfg.get('name', cfg['id']),
                'exp':       exp,
                'days_left': days_left,
            })
    return jsonify(result)

@app.route('/api/weather')
def api_weather():
    return jsonify(get_weather() or {})

@app.route('/api/print_history')
def api_print_history():
    return jsonify(load_print_history())

@app.route('/api/print_history/clear', methods=['POST'])
def api_print_history_clear():
    save_print_history([])
    return jsonify({"ok": True})

@app.route('/api/system/weather_settings', methods=['POST'])
def api_weather_settings():
    data = request.get_json() or {}
    CONFIG['weather_location'] = data.get('location', '').strip()
    CONFIG['weather_unit']     = 'F' if data.get('unit', 'F').upper() == 'F' else 'C'
    save_config_to_disk(CONFIG)
    # Invalidate cache so next fetch uses new settings
    _weather_cache['fetched_at'] = 0
    _weather_cache['data']       = None
    return jsonify({"ok": True})

@app.route('/api/config')
def api_config():
    return jsonify(CONFIG)

@app.route('/api/config/save', methods=['POST'])
def api_config_save():
    global CONFIG, printer_states
    try:
        new_config = request.get_json()
        if not new_config or 'printers' not in new_config:
            return jsonify({"ok": False, "error": "Invalid config format"})

        # Repair missing id fields before validation
        for i, cfg in enumerate(new_config["printers"]):
            if isinstance(cfg, dict) and ("id" not in cfg or not cfg["id"]):
                cfg["id"] = f"printer{i+1}"

        if not validate_and_repair_config(new_config):
            return jsonify({"ok": False, "error": "Invalid config structure"})

        # Backup current known-good config
        try:
            if os.path.exists(CONFIG_PATH):
                shutil.copy2(CONFIG_PATH, KNOWN_GOOD_CONFIG_PATH)
        except Exception:
            pass

        save_config_to_disk(new_config)
        CONFIG = new_config

        with state_lock:
            for cfg in CONFIG["printers"]:
                pid = cfg["id"]
                if pid not in printer_states:
                    printer_states[pid] = default_state(cfg)
                else:
                    printer_states[pid]['name']    = cfg.get('name', pid)
                    printer_states[pid]['mode']    = cfg.get('mode', 'lan')
                    printer_states[pid]['enabled'] = cfg.get('enabled', True)

        def restart_workers():
            time.sleep(1)
            # Disconnect existing clients gracefully
            for pid, client in list(active_clients.items()):
                try:
                    client.loop_stop()
                    client.disconnect()
                except Exception:
                    pass
            active_clients.clear()
            time.sleep(2)
            for cfg in CONFIG["printers"]:
                if cfg.get("enabled", True):
                    t = threading.Thread(target=mqtt_worker, args=(cfg,), daemon=True)
                    t.start()

        threading.Thread(target=restart_workers, daemon=True).start()
        log.info("Config saved and connections restarting")
        return jsonify({"ok": True})

    except Exception as e:
        log.error(f"Config save error: {e}")
        return jsonify({"ok": False, "error": str(e)})
    
# ---------------------------------------------------------------------------
# API — State & Config
# ---------------------------------------------------------------------------
def broadcast_state():
    with state_lock:
        data = [s for s in printer_states.values() if s.get('enabled', True)]
    socketio.emit('state_update', data)

# ---------------------------------------------------------------------------
# API — Display & Thumbnail
# ---------------------------------------------------------------------------
@app.route('/api/display')
def api_display():
    return jsonify(CONFIG.get('display', {}))

@app.route('/api/thumbnail/<printer_id>')
def api_thumbnail(printer_id):
    try:
        printer_cfg = next((p for p in CONFIG["printers"] if p["id"] == printer_id), None)
        if not printer_cfg or printer_cfg.get("mode") != "cloud":
            return jsonify({"ok": False, "error": "Cloud mode only"})

        token  = printer_cfg.get("bambu_token", "")
        serial = printer_cfg.get("serial", "")
        headers = {"Authorization": f"Bearer {token}", "User-Agent": "bambuhelper-rt/1.1"}
        url = f"https://api.bambulab.com/v1/iot-service/api/user/task?deviceId={serial}&limit=3"
        req = urllib.request.Request(url, headers=headers)

        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            log.warning(f"Thumbnail API HTTP error {e.code} for {printer_id}")
            return jsonify({"ok": False, "error": f"HTTP {e.code}"})

        tasks = data.get("hits", data.get("data", []))
        cover_url = next((t["cover"] for t in tasks if t.get("cover")), None)
        if not cover_url:
            return jsonify({"ok": False, "error": "No thumbnail available"})

        img_req = urllib.request.Request(cover_url, headers={"User-Agent": "bambuhelper-rt/1.1"})
        with urllib.request.urlopen(img_req, timeout=8) as img_resp:
            img_data     = img_resp.read()
            content_type = img_resp.headers.get("Content-Type", "image/jpeg")
        return Response(img_data, mimetype=content_type)

    except Exception as e:
        log.warning(f"Thumbnail fetch failed for {printer_id}: {e}")
        return jsonify({"ok": False, "error": str(e)})

# ---------------------------------------------------------------------------
# API — Network (signal status)
# ---------------------------------------------------------------------------
@app.route('/api/network')
def api_network():
    try:
        result = subprocess.run(['iw', 'dev', 'mlan0', 'link'],
                                capture_output=True, text=True, timeout=3)
        output = result.stdout
        signal, ssid = None, None
        m = re.search(r'signal:\s*(-\d+)', output)
        if m: signal = int(m.group(1))
        m = re.search(r'SSID:\s*(.+)', output)
        if m: ssid = m.group(1).strip()
        if signal is None:
            return jsonify({"ok": True, "signal": None, "ssid": ssid, "quality": "unknown"})
        quality = 'good' if signal > -60 else 'fair' if signal > -75 else 'poor'
        return jsonify({"ok": True, "signal": signal, "ssid": ssid, "quality": quality})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

# ---------------------------------------------------------------------------
# API — Display controls
# ---------------------------------------------------------------------------
@app.route('/api/display/brightness', methods=['POST'])
def api_display_brightness():
    try:
        data       = request.get_json()
        brightness = int(data.get('brightness', 128))
        brightness = max(10, min(254, brightness))  # clamp 10-254
        with open('/sys/class/backlight/backlight/brightness', 'w') as f:
            f.write(str(brightness))
        return jsonify({"ok": True, "brightness": brightness})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

# ---------------------------------------------------------------------------
# API — Printer controls
# ---------------------------------------------------------------------------
@app.route('/api/printer/control', methods=['POST'])
def api_printer_control():
    try:
        data       = request.get_json()
        printer_id = data.get('printer_id')
        command    = data.get('command')
        if command not in ('pause', 'resume', 'stop'):
            return jsonify({"ok": False, "error": "Invalid command"})
        printer_cfg = next((p for p in CONFIG["printers"] if p["id"] == printer_id), None)
        if not printer_cfg:
            return jsonify({"ok": False, "error": "Printer not found"})
        serial  = printer_cfg["serial"]
        payload = json.dumps({"print": {"sequence_id": "0", "command": command}})
        host, port, username, password = get_connection_params(printer_cfg)
        def publish():
            import paho.mqtt.publish as publish_mqtt
            tls = ssl.create_default_context()
            tls.check_hostname = False
            tls.verify_mode    = ssl.CERT_NONE
            publish_mqtt.single(
                topic    = f"device/{serial}/request",
                payload  = payload,
                hostname = host,
                port     = port,
                auth     = {"username": username, "password": password},
                tls      = {"context": tls},
                protocol = mqtt.MQTTv311,
                qos      = 1
            )
        threading.Thread(target=publish, daemon=True).start()
        return jsonify({"ok": True})
    except Exception as e:
        log.error(f"Control error: {e}")
        return jsonify({"ok": False, "error": str(e)})

# ---------------------------------------------------------------------------
# API — System controls
# ---------------------------------------------------------------------------
@app.route('/api/system/reboot', methods=['POST'])
def api_system_reboot():
    try:
        subprocess.Popen(['sudo', 'reboot'])
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route('/api/system/shutdown', methods=['POST'])
def api_system_shutdown():
    try:
        subprocess.Popen(['sudo', 'shutdown', 'now'])
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route('/api/system/terminal', methods=['POST'])
def api_system_terminal():
    try:
        subprocess.Popen(
            ['xterm', '-fs', '14', '-bg', 'black', '-fg', 'green'],
            env={'DISPLAY': ':0', 'XAUTHORITY': os.path.expanduser('~/.Xauthority')}
        )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

# ---------------------------------------------------------------------------
# MQTT message parser
# ---------------------------------------------------------------------------
BAMBU_ERRORS = {
    0x05000001: "Filament runout",
    0x05000002: "Filament jam detected",
    0x0500000B: "Nozzle temperature abnormal",
    0x0500000C: "Bed temperature abnormal",
    0x05000010: "Fan failure detected",
    0x05000011: "Chamber temperature too high",
    0x05000014: "Heatbreak cooling failed",
    0x05000015: "Extruder motor abnormal",
    0x05000016: "Print head door open",
    0x05008051: "Build plate type does not match Gcode — adjust slicer settings or use correct plate",
    0x0C00000F: "AMS filament mismatch",
    0x0C000010: "AMS communication error",
    0x0C000011: "AMS slot empty",
    0x0C000012: "AMS hub communication error",
}

STAGE_MAP = {
    -1: '', 0: 'Printing', 1: 'Auto bed leveling', 2: 'Heatbed preheating',
    3: 'Sweeping XY mech mode', 4: 'Changing filament', 5: 'M400 pause',
    6: 'Paused (filament runout)', 7: 'Heating nozzle', 8: 'Calibrating extrusion',
    9: 'Scanning bed surface', 10: 'Inspecting first layer', 11: 'Identifying build plate',
    12: 'Calibrating micro lidar', 13: 'Home toolhead', 14: 'Cleaning nozzle tip',
    15: 'Checking extruder temp', 16: 'Paused (user)', 17: 'Paused (front cover fall)',
    18: 'Calibrating micro lidar', 19: 'Paused (nozzle temp)', 20: 'Paused (heat bed temp)',
    21: 'Filament unloading', 22: 'Skip step pause', 23: 'Filament loading',
    24: 'Motor noise calibration', 25: 'Paused (AMS lost)',
    26: 'Paused (low speed of heat break fan)', 27: 'Paused (chamber temp)',
    28: 'Cooling chamber', 29: 'Paused (user gcode)', 30: 'Motor noise showoff',
    31: 'Nozzle filament covered', 32: 'Cutter error', 33: 'First layer error',
    34: 'Nozzle clog', 64: 'Changing filament', 255: '',
}

# Known nozzle/temp field names — used to detect undiscovered H2D fields in payloads
_KNOWN_TEMP_FIELDS = {
    'nozzle_temper', 'nozzle_target_temper', 'bed_temper', 'bed_target_temper',
    'chamber_temper', 'nozzle_type', 'nozzle_diameter', 'extruder',
    'left_nozzle_temper',  'right_nozzle_temper',
    'nozzle_temper_l',     'nozzle_temper_r',
    'nozzle_temper0',      'nozzle_temper1',
    'nozzle_temp_left',    'nozzle_temp_right',
    'left_nozzle_target_temper',  'right_nozzle_target_temper',
    'nozzle_target_temper_l',     'nozzle_target_temper_r',
    'nozzle_target_temper0',      'nozzle_target_temper1',
}

def _find_extruder(obj, depth=0):
    """Recursively search for 'extruder' key in nested dicts (like memmem on raw bytes)."""
    if depth > 5 or not isinstance(obj, dict):
        return None
    if 'extruder' in obj and isinstance(obj['extruder'], dict):
        return obj['extruder']
    for v in obj.values():
        if isinstance(v, dict):
            found = _find_extruder(v, depth + 1)
            if found:
                return found
    return None

def _parse_extruder(state, ext):
    """Parse H2D/H2C extruder object — packed 32-bit temps and active nozzle."""
    if not isinstance(ext, dict):
        return
    # Only update active nozzle when 'state' key is explicitly present
    # (delta messages often omit it — defaulting to 0 would wrongly set R)
    active_nozzle = None
    if 'state' in ext:
        try:
            active_nozzle = (int(ext['state']) >> 4) & 0x0F
            if active_nozzle > 1:
                active_nozzle = 0
            state['nozzle_active_side'] = 'R' if active_nozzle == 0 else 'L'
        except (ValueError, TypeError):
            pass
    # Use previously determined active side if state was absent
    if active_nozzle is None:
        side = state.get('nozzle_active_side')
        active_nozzle = 0 if side == 'R' else 1 if side == 'L' else None
    info = ext.get('info', [])
    if len(info) >= 2:
        is_dual_hw = state.get('nozzle_type', '').upper().startswith('HH')
        if is_dual_hw:
            state['has_dual_nozzle'] = True
        # Store temps for BOTH nozzles + set main nozzle_temp to active one
        for entry in info:
            nid = entry.get('id', -1)
            packed = entry.get('temp')
            if packed is not None:
                try:
                    packed = int(packed)
                    actual = round(float(packed & 0xFFFF), 1)
                    target = round(float((packed >> 16) & 0xFFFF), 1)
                    if nid == 0:    # Right nozzle
                        state['nozzle_temp_r']   = actual
                        state['nozzle_target_r'] = target
                    elif nid == 1:  # Left nozzle
                        state['nozzle_temp_l']   = actual
                        state['nozzle_target_l'] = target
                    if active_nozzle is not None and nid == active_nozzle:
                        state['nozzle_temp']   = actual
                        state['nozzle_target'] = target
                except (ValueError, TypeError):
                    pass
            # snow = flat AMS tray index feeding this nozzle (65535 = none)
            # Only use snow on H2D where encoding is confirmed correct (0-15 = tray index).
            # H2C uses offset values (257, 259, etc.) — unreliable; rely on tray_now instead.
            if state.get('has_dual_nozzle') and active_nozzle is not None and nid == active_nozzle:
                snow = entry.get('snow')
                if snow is not None:
                    try:
                        snow = int(snow)
                        if 0 <= snow <= 15:
                            new_id = f"{snow // 4}-{snow % 4}"
                            if state.get('ams_active_id') != new_id:
                                log.info(f"[{state['id']}] extruder snow={snow} -> ams_active_id={new_id}")
                            state['ams_active_id'] = new_id
                        elif snow in (254, 255, 65535):
                            state['ams_active_id'] = None
                    except (ValueError, TypeError):
                        pass

def parse_print_message(state, msg):
    p = msg.get('print', {})

    # Store raw payload for debug inspection
    last_payloads[state['id']] = p
    # Also keep the last payload that contained temperature data (not overwritten by heartbeats)
    if 'nozzle_temper' in p:
        last_rich_payloads[state['id']] = p
    if 'ams' in p:
        last_ams_payloads[state['id']] = p

    # For dual-nozzle (H2D): nozzle_temper is the INACTIVE nozzle — skip it.
    # Active nozzle temp comes from the extruder object (parsed below or at root level).
    # For single-nozzle printers: use nozzle_temper normally.
    if not state.get('has_dual_nozzle'):
        if 'nozzle_temper' in p:
            state['nozzle_temp'] = round(float(p['nozzle_temper']), 1)
        if 'nozzle_target_temper' in p:
            state['nozzle_target'] = round(float(p['nozzle_target_temper']), 1)
    else:
        # H2D: only use nozzle_temper as fallback when idle (no extruder data)
        if 'nozzle_temper' in p and p.get('gcode_state') in ('IDLE', 'FINISH', None):
            state['nozzle_temp'] = round(float(p['nozzle_temper']), 1)
        if 'nozzle_target_temper' in p and p.get('gcode_state') in ('IDLE', 'FINISH', None):
            state['nozzle_target'] = round(float(p['nozzle_target_temper']), 1)

    # Clear active side when printer finishes or goes idle
    if p.get('gcode_state') in ('IDLE', 'FINISH'):
        state['nozzle_active_side'] = None
        state['ams_active_id']      = None

    # Parse extruder from inside print object (if present)
    if 'extruder' in p:
        _parse_extruder(state, p['extruder'])

    # Log any unhandled field that looks temperature/nozzle related — helps identify H2D fields
    for k, v in p.items():
        if ('nozzle' in k or 'temper' in k) and k not in _KNOWN_TEMP_FIELDS:
            log.info(f"[{state['id']}] UNKNOWN nozzle/temp field: {k} = {v!r}")
    if 'bed_temper'           in p: state['bed_temp']        = round(float(p['bed_temper']), 1)
    if 'bed_target_temper'    in p: state['bed_target']      = round(float(p['bed_target_temper']), 1)
    if 'chamber_temper'       in p: state['chamber_temp']    = round(float(p['chamber_temper']), 1)
    if 'cooling_fan_speed'    in p: state['fan_part']        = round((int(p['cooling_fan_speed']) / 15) * 100)
    if 'big_fan1_speed'       in p: state['fan_aux']         = round((int(p['big_fan1_speed'])    / 15) * 100)
    if 'big_fan2_speed'       in p: state['fan_chamber']     = round((int(p['big_fan2_speed'])    / 15) * 100)
    if 'mc_percent'           in p: state['progress']        = int(p['mc_percent'])
    if 'layer_num'            in p: state['layer_current']   = int(p['layer_num'])
    if 'total_layer_num'      in p: state['layer_total']     = int(p['total_layer_num'])
    if 'mc_remaining_time'    in p: state['time_remaining']  = int(p['mc_remaining_time'])
    if 'subtask_name'         in p: state['print_name']      = p.get('subtask_name', '')
    if 'spd_lvl'              in p: state['spd_lvl']          = int(p['spd_lvl'])
    if 'stg_cur'              in p: state['stage']            = STAGE_MAP.get(int(p['stg_cur']), '')
    if 'nozzle_type'          in p:
        state['nozzle_type'] = p.get('nozzle_type', '')
        # HH = H2D dual hotend; mark so dashboard always shows L/R bars
        if state['nozzle_type'].upper().startswith('HH'):
            state['has_dual_nozzle'] = True
    if 'nozzle_diameter'      in p: state['nozzle_diameter'] = p.get('nozzle_diameter', '')

    # Parse AMS tray data
    if 'ams' in p and isinstance(p['ams'], dict):
        ams_data = p['ams']
        ams_list = ams_data.get('ams', [])
        # tray_now: flat index of the currently feeding tray.
        # 0-3 = AMS1 slots 0-3, 4-7 = AMS2 slots 0-3, 254/255 = external/none.
        tray_now = ams_data.get('tray_now')
        if tray_now is not None:
            try:
                tray_now = int(tray_now)
            except (ValueError, TypeError):
                tray_now = None
        # Update persisted active_id when tray_now is explicitly reported
        if tray_now is not None:
            if tray_now < 254:
                new_active = f"{tray_now // 4}-{tray_now % 4}"
                # During a print job, only trust tray_now if it maps to a job tray
                # (H2C firmware can transiently report non-job trays during changes)
                job_slots = state.get('ams_job_slots', [])
                if not job_slots or new_active in job_slots:
                    state['ams_active_id'] = new_active
                    log.info(f"[{state['id']}] AMS tray_now={tray_now} -> ams_active_id={new_active}")
                else:
                    log.debug(f"[{state['id']}] AMS tray_now={tray_now} -> {new_active} not in job, ignoring")
            else:
                state['ams_active_id'] = None  # 254/255 = no AMS tray active
        # Use persisted active_id so the indicator survives payloads that omit tray_now
        active_id = state.get('ams_active_id')
        trays = []
        for ams_unit in ams_list:
            dry_setting = ams_unit.get('dry_setting') or {}
            dry_time    = int(ams_unit.get('dry_time', 0))
            is_drying   = dry_time > 0 or int(dry_setting.get('dry_temperature', -1)) > 0
            dry_h       = dry_time // 60
            dry_m       = dry_time % 60
            for tray in ams_unit.get('tray', []):
                color     = tray.get('tray_color', '00000000')
                hex_color = f"#{color[:6]}" if len(color) >= 6 else '#888888'
                tray_id   = f"{ams_unit.get('id','0')}-{tray.get('id','0')}"
                has_fil   = bool(tray.get('tray_info_idx'))
                trays.append({
                    'id':           tray_id,
                    'color':        hex_color,
                    'type':         tray.get('tray_info_idx', ''),
                    'name':         tray.get('tray_id_name', ''),
                    'remain':       tray.get('remain', -1),
                    'temp':         ams_unit.get('temp', ''),
                    'humidity':     ams_unit.get('humidity', ''),
                    'humidity_pct': ams_unit.get('humidity_raw', ''),
                    'drying':       is_drying,
                    'dry_remain':   f"{dry_h}:{dry_m:02d}" if is_drying and dry_time > 0 else '',
                    'state':        tray.get('state', 0),
                    'active':       (active_id is not None and tray_id == active_id) or
                                    (has_fil and tray.get('state') == 27),
                    'in_job':       False,
                })
        if trays:
            state['ams_trays'] = trays
            # Apply any previously stored job slot mapping
            if state.get('ams_job_slots'):
                for tray in state['ams_trays']:
                    tray['in_job'] = tray['id'] in state['ams_job_slots']

    # Refresh active flags on existing trays whenever ams_active_id changes
    # (tray_now can arrive without full tray data, leaving baked-in flags stale)
    if state.get('ams_trays'):
        aid = state.get('ams_active_id')
        for tray in state['ams_trays']:
            tray['active'] = (aid is not None and tray['id'] == aid) or \
                             (bool(tray.get('type')) and tray.get('state') == 27)

    # Mark which trays are part of the current print job using mapping field
    if 'mapping' in p:
        active_slots = set()
        for m in p['mapping']:
            if m != 65535 and m != 0:
                ams_id  = (m >> 8) & 0xFF
                tray_id = m & 0xFF
                active_slots.add(f"{ams_id}-{tray_id}")
        state['ams_job_slots'] = list(active_slots)
        if state.get('ams_trays'):
            for tray in state['ams_trays']:
                tray['in_job'] = tray['id'] in active_slots

    # Parse virtual slots (left/right nozzle filament info + possible temps)
    if 'vir_slot' in p:
        slots = p['vir_slot']
        vir = []
        for i, s in enumerate(slots[:2]):
            color     = s.get('tray_color', '00000000')
            hex_color = f"#{color[:6]}" if len(color) >= 6 else '#888888'
            # Alpha == 00 means no filament loaded in this nozzle
            is_empty  = len(color) >= 8 and color[6:8].upper() == '00'
            # Capture any temperature field present in vir_slot entries
            slot_temp   = s.get('nozzle_temper') or s.get('temper') or s.get('temp')
            slot_target = s.get('nozzle_target_temper') or s.get('target_temper')
            if slot_temp is not None:
                key = 'nozzle_temp_l' if i == 0 else 'nozzle_temp_r'
                state[key] = round(float(slot_temp), 1)
                log.info(f"[{state['id']}] vir_slot[{i}] temp={slot_temp}")
            if slot_target is not None:
                key = 'nozzle_target_l' if i == 0 else 'nozzle_target_r'
                state[key] = round(float(slot_target), 1)
            # Log any unrecognised vir_slot fields for discovery
            for k, v in s.items():
                if k not in {'tray_color','tray_type','tray_diameter','id',
                             'nozzle_temper','temper','temp',
                             'nozzle_target_temper','target_temper'}:
                    log.info(f"[{state['id']}] vir_slot[{i}] unknown field: {k} = {v!r}")
            vir.append({
                'color':    hex_color,
                'type':     s.get('tray_type', ''),
                'diameter': s.get('tray_diameter', '1.75'),
                'id':       s.get('id', ''),
                'empty':    is_empty,
            })
        state['vir_slots'] = vir

    if 'gcode_state' in p:
        prev_state = state.get('gcode_state', '')
        state['gcode_state'] = p['gcode_state']
        state['printing']    = p['gcode_state'] in ('RUNNING', 'PAUSE')
        # Track print start time
        if p['gcode_state'] == 'RUNNING' and prev_state not in ('RUNNING', 'PAUSE'):
            state['print_start_time'] = int(time.time())

    if 'print_error' in p:
        if p['print_error'] != 0:
            code     = p['print_error']
            msg_text = BAMBU_ERRORS.get(code, f"Error code: {hex(code)}")
            # Format as partial HMS code (upper/lower 16-bit words) for wiki QR link
            hms_code = f"{(code >> 16) & 0xFFFF:04X}-{code & 0xFFFF:04X}-0000-0000"
            entry    = {'code': hms_code, 'msg': msg_text}
            if not any(e.get('msg') == msg_text if isinstance(e, dict) else e == msg_text
                       for e in state['errors']):
                state['errors'].append(entry)
                if len(state['errors']) > 5:
                    state['errors'].pop(0)
        else:
            state['errors'] = []  # print_error cleared to 0 means error resolved

    if p.get('gcode_state') == 'RUNNING':
        if state.get('errors'):
            state['errors'] = []      # resuming from pause/error clears stale print_error
        if state.get('hms_errors'):
            state['hms_errors'] = []  # transient HMS codes at print start clear once running

    if 'hms' in p:
        hms_list = p['hms']
        if isinstance(hms_list, list) and hms_list:
            formatted = []
            for h in hms_list:
                attr = h.get('attr', 0)
                code = h.get('code', 0)
                a1 = (attr >> 16) & 0xFFFF
                a2 = attr & 0xFFFF
                c1 = (code >> 16) & 0xFFFF
                c2 = code & 0xFFFF
                formatted.append(f"{a1:04X}-{a2:04X}-{c1:04X}-{c2:04X}")
            log.info(f"[{state['id']}] HMS codes received: {formatted}")
            dismissed = state.get('dismissed_hms', [])
            # If any code is genuinely new (not previously dismissed), clear the dismissed list
            new_codes = [c for c in formatted if c not in dismissed]
            if new_codes:
                state['dismissed_hms'] = []
                dismissed = []
            active = [c for c in formatted if c not in dismissed]
            # Enrich with descriptions (uses cache — no repeated network calls)
            state['hms_errors'] = _enrich_hms_codes(active)
        else:
            state['hms_errors']    = []
            state['dismissed_hms'] = []

    if p.get('gcode_state') == 'FINISH':
        record_print_finished(state)
        state['errors']          = []
        state['hms_errors']      = []
        state['dismissed_hms']   = []
        state['print_start_time'] = None
        _dismissed_hms_store.pop(state['id'], None)
        save_dismissed_hms(_dismissed_hms_store)
    state['last_update'] = time.time()

# ---------------------------------------------------------------------------
# MQTT client factory
# ---------------------------------------------------------------------------
def make_mqtt_client(printer_cfg):
    printer_id = printer_cfg["id"]
    serial     = printer_cfg["serial"]
    mode       = printer_cfg.get("mode", "lan")
    host, port, username, password = get_connection_params(printer_cfg)

    def on_connect(client, userdata, flags, rc):
        if rc == 0:
            log.info(f"[{printer_id}] Connected ({mode} mode) → {host}")
            with state_lock:
                printer_states[printer_id]['connected'] = True
            client.subscribe(f"device/{serial}/report")
            pushall = json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}})
            client.publish(f"device/{serial}/request", pushall)
            broadcast_state()
        else:
            reasons = {
                1: "Bad protocol version", 2: "Client ID rejected",
                3: "Broker unavailable",
                4: "Bad credentials — check access_code or bambu_token",
                5: "Not authorised — check bambu_user_id or token",
            }
            log.warning(f"[{printer_id}] Connect failed: {reasons.get(rc, f'rc={rc}')}")

    def on_disconnect(client, userdata, rc):
        log.warning(f"[{printer_id}] Disconnected rc={rc}")
        with state_lock:
            printer_states[printer_id]['connected'] = False
        broadcast_state()

    def on_message(client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode('utf-8'))
            with state_lock:
                if 'print' in payload:
                    parse_print_message(printer_states[printer_id], payload)
                # Search for extruder anywhere in the message (root, print.device, etc.)
                # Keralots uses memmem on raw bytes — it finds extruder regardless of nesting
                state = printer_states[printer_id]
                ext = _find_extruder(payload)
                if ext is not None:
                    _parse_extruder(state, ext)
            broadcast_state()
        except Exception as e:
            log.error(f"[{printer_id}] Parse error: {e}")

    client = mqtt.Client(
        client_id=f"bambuhelper_{printer_id}_{int(time.time())}",
        protocol=mqtt.MQTTv311
    )
    client.username_pw_set(username, password)
    tls_ctx = ssl.create_default_context()
    tls_ctx.check_hostname = False
    tls_ctx.verify_mode    = ssl.CERT_NONE
    client.tls_set_context(tls_ctx)
    client.on_connect    = on_connect
    client.on_disconnect = on_disconnect
    client.on_message    = on_message
    active_clients[printer_id] = client
    return client, host, port

# ---------------------------------------------------------------------------
# MQTT worker
# ---------------------------------------------------------------------------
def mqtt_worker(printer_cfg):
    printer_id = printer_cfg["id"]
    if not printer_cfg.get("enabled", True):
        log.info(f"[{printer_id}] Disabled, skipping")
        return
    retry_delay = 5
    while True:
        client, host, port = make_mqtt_client(printer_cfg)
        try:
            log.info(f"[{printer_id}] Connecting to {host}:{port}")
            client.connect(host, port, keepalive=60)
            client.loop_start()
            retry_delay = 5
            while True:
                time.sleep(10)
                with state_lock:
                    last      = printer_states[printer_id]['last_update']
                    connected = printer_states[printer_id]['connected']
                if connected and last > 0 and (time.time() - last) > 60:
                    log.warning(f"[{printer_id}] No messages for 60s, reconnecting")
                    break
        except Exception as e:
            log.error(f"[{printer_id}] Error: {e}")
        finally:
            try:
                client.loop_stop()
                client.disconnect()
            except Exception:
                pass
            with state_lock:
                printer_states[printer_id]['connected'] = False
            broadcast_state()
        log.info(f"[{printer_id}] Retry in {retry_delay}s")
        time.sleep(retry_delay)
        retry_delay = min(retry_delay * 2, 60)

# ---------------------------------------------------------------------------
# Periodic broadcast
# ---------------------------------------------------------------------------
def periodic_broadcast():
    while True:
        time.sleep(5)
        broadcast_state()

# ---------------------------------------------------------------------------
# Display timeout monitor
# ---------------------------------------------------------------------------
def display_monitor():
    DISPLAY_ENV = {'DISPLAY': ':0', 'XAUTHORITY': os.path.expanduser('~/.Xauthority')}

    def screen_on():
        subprocess.run(['xset', '-display', ':0', 'dpms', 'force', 'on'],
                       env=DISPLAY_ENV, capture_output=True)

    def screen_off():
        subprocess.run(['xset', '-display', ':0', 'dpms', 'force', 'off'],
                       env=DISPLAY_ENV, capture_output=True)

    subprocess.run(['xset', '-display', ':0', '+dpms'], env=DISPLAY_ENV, capture_output=True)
    screen_on()
    all_done_since = None

    while True:
        time.sleep(30)
        try:
            display     = CONFIG.get('display', {})
            always_on   = display.get('always_on', False)
            timeout_min = int(display.get('timeout', 3))
            show_clock  = display.get('show_clock', True)
            if always_on or timeout_min == 0:
                screen_on()
                all_done_since = None
                continue
            with state_lock:
                active = any(
                    s.get('printing', False)
                    for s in printer_states.values()
                    if s.get('enabled', True)
                )
            if active:
                screen_on()
                all_done_since = None
            else:
                if all_done_since is None:
                    all_done_since = time.time()
                elif time.time() - all_done_since >= timeout_min * 60:
                    screen_off()
        except Exception as e:
            log.warning(f"Display monitor error: {e}")

# ---------------------------------------------------------------------------
# API — Network management (scan, connect, IP config)
# ---------------------------------------------------------------------------
@app.route('/api/network/status')
def api_network_status():
    try:
        # Current connection
        conn = subprocess.run(
            ['nmcli', '-t', '-f', 'NAME,TYPE,STATE,DEVICE', 'connection', 'show', '--active'],
            capture_output=True, text=True, timeout=5
        )
        ip = subprocess.run(
            ['nmcli', '-t', '-f', 'IP4.ADDRESS,IP4.GATEWAY', 'device', 'show', 'mlan0'],
            capture_output=True, text=True, timeout=5
        )
        ssid, ip_addr, gateway = None, None, None
        for line in conn.stdout.splitlines():
            parts = line.split(':')
            if len(parts) >= 3 and parts[1] == '802-11-wireless':
                ssid = parts[0]
        for line in ip.stdout.splitlines():
            if 'IP4.ADDRESS' in line:
                ip_addr = line.split(':')[1].split('/')[0]
            if 'IP4.GATEWAY' in line:
                gateway = line.split(':')[1]
        return jsonify({"ok": True, "ssid": ssid, "ip": ip_addr, "gateway": gateway})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route('/api/network/scan')
def api_network_scan():
    try:
        result = subprocess.run(
            ['nmcli', '-t', '-f', 'SSID,SIGNAL,SECURITY,IN-USE', 'device', 'wifi', 'list', '--rescan', 'yes'],
            capture_output=True, text=True, timeout=15
        )
        networks = []
        seen = set()
        for line in result.stdout.splitlines():
            parts = line.split(':')
            if len(parts) >= 3 and parts[0] and parts[0] not in seen:
                seen.add(parts[0])
                networks.append({
                    "ssid":     parts[0],
                    "signal":   int(parts[1]) if parts[1].isdigit() else 0,
                    "security": parts[2] or 'Open',
                    "active":   parts[3] == '*' if len(parts) > 3 else False
                })
        networks.sort(key=lambda x: x['signal'], reverse=True)
        return jsonify({"ok": True, "networks": networks})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route('/api/network/connect', methods=['POST'])
def api_network_connect():
    try:
        data     = request.get_json()
        ssid     = data.get('ssid', '').strip()
        password = data.get('password', '').strip()
        if not ssid:
            return jsonify({"ok": False, "error": "SSID required"})

        # Get current SSID for fallback
        current = subprocess.run(
            ['nmcli', '-t', '-f', 'NAME,TYPE,STATE', 'connection', 'show', '--active'],
            capture_output=True, text=True, timeout=5
        )
        current_ssid = None
        for line in current.stdout.splitlines():
            parts = line.split(':')
            if len(parts) >= 2 and parts[1] == '802-11-wireless':
                current_ssid = parts[0]

        # Attempt connection
        cmd = ['sudo', 'nmcli', 'device', 'wifi', 'connect', ssid]
        if password:
            cmd += ['password', password]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode == 0:
            return jsonify({"ok": True, "message": f"Connected to {ssid}"})
        else:
            # Failed — try to reconnect to previous network
            if current_ssid and current_ssid != ssid:
                subprocess.run(
                    ['sudo', 'nmcli', 'connection', 'up', current_ssid],
                    capture_output=True, timeout=15
                )
            return jsonify({"ok": False, "error": result.stderr.strip() or "Connection failed"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route('/api/network/forget', methods=['POST'])
def api_network_forget():
    try:
        data = request.get_json()
        ssid = data.get('ssid', '').strip()
        if not ssid:
            return jsonify({"ok": False, "error": "SSID required"})
        result = subprocess.run(
            ['sudo', 'nmcli', 'connection', 'delete', ssid],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return jsonify({"ok": True})
        else:
            return jsonify({"ok": False, "error": result.stderr.strip()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route('/api/network/ipconfig', methods=['GET'])
def api_network_ipconfig():
    try:
        # Detect the active WiFi connection name dynamically
        active = subprocess.run(
            ['nmcli', '-t', '-f', 'NAME,TYPE', 'connection', 'show', '--active'],
            capture_output=True, text=True, timeout=5
        )
        conn_name = None
        for line in active.stdout.splitlines():
            parts = line.split(':')
            if len(parts) >= 2 and parts[1] == '802-11-wireless':
                conn_name = parts[0]
                break
        if not conn_name:
            return jsonify({"ok": False, "error": "No active WiFi connection found"})
        result = subprocess.run(
            ['nmcli', '-t', 'connection', 'show', conn_name],
            capture_output=True, text=True, timeout=5
        )
        method, address, gateway, dns = 'auto', '', '', ''
        for line in result.stdout.splitlines():
            if line.startswith('ipv4.method:'):
                method = line.split(':')[1].strip()
            elif line.startswith('ipv4.addresses:'):
                address = line.split(':')[1].strip()
            elif line.startswith('ipv4.gateway:'):
                val = line.split(':')[1].strip()
                gateway = '' if val == '--' else val
            elif line.startswith('ipv4.dns:'):
                val = line.split(':')[1].strip()
                dns = '' if val == '--' else val
        return jsonify({"ok": True, "ssid": conn_name, "method": method, "address": address,
                        "gateway": gateway, "dns": dns})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route('/api/network/ipconfig', methods=['POST'])
def api_network_ipconfig_save():
    try:
        data   = request.get_json()
        ssid   = data.get('ssid', '').strip()
        method = data.get('method', 'auto')

        # If no ssid provided, detect the active WiFi connection
        if not ssid:
            active = subprocess.run(
                ['nmcli', '-t', '-f', 'NAME,TYPE', 'connection', 'show', '--active'],
                capture_output=True, text=True, timeout=5
            )
            for line in active.stdout.splitlines():
                parts = line.split(':')
                if len(parts) >= 2 and parts[1] == '802-11-wireless':
                    ssid = parts[0]
                    break
        if not ssid:
            return jsonify({"ok": False, "error": "No active WiFi connection found"})

        if method == 'auto':
            subprocess.run(['sudo', 'nmcli', 'connection', 'modify', ssid,
                           'ipv4.method', 'auto',
                           'ipv4.addresses', '',
                           'ipv4.gateway', '',
                           'ipv4.dns', ''],
                          capture_output=True, timeout=10)
        else:
            address = data.get('address', '')
            gateway = data.get('gateway', '')
            dns     = data.get('dns', '8.8.8.8,8.8.4.4')
            if not address:
                return jsonify({"ok": False, "error": "IP address required"})
            subprocess.run(['sudo', 'nmcli', 'connection', 'modify', ssid,
                           'ipv4.method', 'manual',
                           'ipv4.addresses', address,
                           'ipv4.gateway', gateway,
                           'ipv4.dns', dns],
                          capture_output=True, timeout=10)

        # Reconnect to apply changes
        result = subprocess.run(['sudo', 'nmcli', 'connection', 'up', ssid],
                               capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            return jsonify({"ok": True})
        else:
            return jsonify({"ok": False, "error": result.stderr.strip()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

# ---------------------------------------------------------------------------
# API — Debug: last raw MQTT print payload (field discovery)
# ---------------------------------------------------------------------------
@app.route('/api/debug/last_payload/<printer_id>')
def api_debug_last_payload(printer_id):
    payload = last_rich_payloads.get(printer_id) or last_payloads.get(printer_id)
    if payload is None:
        return jsonify({"ok": False, "error": "No payload received yet"})
    # Scalar fields + vir_slot + extruder for inspection
    simple = {k: v for k, v in payload.items()
              if not isinstance(v, (dict, list)) or k in ('vir_slot', 'extruder', 'device')}
    return jsonify({"ok": True, "printer_id": printer_id,
                    "from_rich": printer_id in last_rich_payloads, "fields": simple})

@app.route('/api/debug/ams/<printer_id>')
def api_debug_ams(printer_id):
    payload = last_ams_payloads.get(printer_id)
    if payload is None:
        return jsonify({"ok": False, "error": "No AMS payload received yet"})
    ams = payload.get('ams', {})
    return jsonify({"ok": True, "printer_id": printer_id,
                    "tray_now": ams.get('tray_now'),
                    "ams_units": [
                        {"id": u.get('id'), "temp": u.get('temp'), "humidity": u.get('humidity'),
                         "trays": [{"id": t.get('id'), "type": t.get('tray_info_idx'),
                                    "color": t.get('tray_color'), "remain": t.get('remain'),
                                    "state": t.get('state')} for t in u.get('tray', [])]}
                        for u in ams.get('ams', [])
                    ]})

# ---------------------------------------------------------------------------
# API — Debug: force printer state (in-memory only, overwritten by next MQTT)
# ---------------------------------------------------------------------------
@app.route('/api/debug/force_state/<printer_id>', methods=['POST'])
def api_debug_force_state(printer_id):
    data = request.get_json() or {}
    if printer_id not in printer_states:
        return jsonify({"ok": False, "error": "Unknown printer"})
    with state_lock:
        state = printer_states[printer_id]
        if 'gcode_state' in data:
            state['gcode_state'] = data['gcode_state']
        if 'hms_errors' in data:
            state['hms_errors'] = data['hms_errors']
    broadcast_state()
    return jsonify({"ok": True, "printer_id": printer_id, "applied": data})

# ---------------------------------------------------------------------------
# API — Bambu Cloud login (token fetch proxy)
# ---------------------------------------------------------------------------
@app.route('/api/system/bambu_login', methods=['POST'])
def api_bambu_login():
    data     = request.get_json() or {}
    email    = data.get('email', '').strip()
    password = data.get('password', '').strip()
    code     = data.get('code', '').strip()
    if not email or not password:
        return jsonify({"ok": False, "error": "Email and password required"})
    payload = {"account": email, "password": password, "apiError": ""}
    if code:
        payload["code"] = code
    try:
        req = urllib.request.Request(
            "https://api.bambulab.com/v1/user-service/user/login",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "bambu_network_agent/01.09.05.01",
                "Accept": "application/json",
                "App-Language": "en",
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
        if result.get("loginType") == "verifyCode":
            return jsonify({"ok": False, "needs_code": True,
                            "message": "Verification code sent — check your email"})
        token = result.get("accessToken", "")
        if token:
            return jsonify({"ok": True, "token": token})
        return jsonify({"ok": False, "error": result.get("message", "Login failed — check credentials")})
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read())
            return jsonify({"ok": False, "error": err.get("message", str(e))})
        except Exception:
            return jsonify({"ok": False, "error": str(e)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

# ---------------------------------------------------------------------------
# API — PIN authentication
# ---------------------------------------------------------------------------
@app.route('/api/auth/pin', methods=['POST'])
def api_auth_pin():
    data = request.get_json() or {}
    if str(data.get('pin', '')) == settings_pin():
        session.permanent = True
        session['pin_authenticated'] = True
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Incorrect PIN"})

@app.route('/api/auth/logout', methods=['POST'])
def api_auth_logout():
    session.pop('pin_authenticated', None)
    return jsonify({"ok": True})

# ---------------------------------------------------------------------------
# API — Settings backup and restore
# ---------------------------------------------------------------------------
@app.route('/api/settings/backup')
def api_settings_backup():
    try:
        buf = io.BytesIO(json.dumps(CONFIG, indent=2).encode())
        buf.seek(0)
        return send_file(buf, mimetype='application/json',
                         as_attachment=True, download_name='bambuhelper_config.json')
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route('/api/settings/restore', methods=['POST'])
def api_settings_restore():
    try:
        f = request.files.get('config')
        if not f:
            return jsonify({"ok": False, "error": "No file uploaded"})
        data = json.loads(f.read())
        if not validate_and_repair_config(data):
            return jsonify({"ok": False, "error": "Invalid config file"})
        # Preserve secret key and access settings from current config
        data.setdefault('secret_key', CONFIG.get('secret_key', secrets.token_hex(32)))
        CONFIG.clear()
        CONFIG.update(data)
        save_config_to_disk(CONFIG)
        return jsonify({"ok": True, "message": "Config restored — reboot to apply"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

# ---------------------------------------------------------------------------
# API — LAN access and PIN management
# ---------------------------------------------------------------------------
@app.route('/api/system/lan_access', methods=['POST'])
def api_system_lan_access():
    data = request.get_json() or {}
    CONFIG['lan_access'] = bool(data.get('enabled', False))
    save_config_to_disk(CONFIG)
    return jsonify({"ok": True, "lan_access": CONFIG['lan_access']})

@app.route('/api/system/set_pin', methods=['POST'])
def api_system_set_pin():
    data = request.get_json() or {}
    new_pin = str(data.get('pin', '')).strip()
    CONFIG['settings_pin'] = new_pin
    save_config_to_disk(CONFIG)
    if new_pin:
        session['pin_authenticated'] = True  # keep current session authenticated
    return jsonify({"ok": True, "pin_set": bool(new_pin)})

@app.route('/api/system/access_info')
def api_system_access_info():
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        local_ip = '?.?.?.?'
    return jsonify({"ok": True, "lan_access": lan_access_enabled(),
                    "pin_set": bool(settings_pin()),
                    "pin_protect_local": pin_protect_local(),
                    "local_ip": local_ip})

@app.route('/api/system/pin_protect_local', methods=['POST'])
def api_system_pin_protect_local():
    data = request.get_json() or {}
    CONFIG['pin_protect_local'] = bool(data.get('enabled', True))
    save_config_to_disk(CONFIG)
    return jsonify({"ok": True, "pin_protect_local": CONFIG['pin_protect_local']})

# ---------------------------------------------------------------------------
# API — Clear printer errors (manual dismiss for stale cloud MQTT errors)
# ---------------------------------------------------------------------------
@app.route('/api/printer/clear_errors', methods=['POST'])
def api_printer_clear_errors():
    try:
        printer_id = request.get_json().get('printer_id')
        with state_lock:
            if printer_id not in printer_states:
                return jsonify({"ok": False, "error": "Printer not found"})
            raw_hms = printer_states[printer_id].get('hms_errors', [])
            # hms_errors may be [{"code":…,"desc":…}] dicts or legacy plain strings
            codes = [e['code'] if isinstance(e, dict) else e for e in raw_hms]
            printer_states[printer_id]['dismissed_hms'] = codes
            printer_states[printer_id]['errors']        = []
            printer_states[printer_id]['hms_errors']    = []
        # Persist so dismiss survives reboot
        _dismissed_hms_store[printer_id] = codes
        save_dismissed_hms(_dismissed_hms_store)
        broadcast_state()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

# ---------------------------------------------------------------------------
# API — Battery / power supply
# ---------------------------------------------------------------------------
@app.route('/api/system/battery')
def api_system_battery():
    try:
        base = '/sys/class/power_supply'
        for name in os.listdir(base):
            uevent_path = f'{base}/{name}/uevent'
            if not os.path.exists(uevent_path):
                continue
            props = {}
            with open(uevent_path) as f:
                for line in f:
                    if '=' in line:
                        k, v = line.strip().split('=', 1)
                        props[k] = v
            if props.get('POWER_SUPPLY_TYPE', '').upper() == 'BATTERY':
                capacity = int(props.get('POWER_SUPPLY_CAPACITY', 0))
                status   = props.get('POWER_SUPPLY_STATUS', 'Unknown')
                return jsonify({"ok": True, "capacity": capacity, "status": status})
        return jsonify({"ok": False, "error": "No battery found"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

# ---------------------------------------------------------------------------
# API — Timezone
# ---------------------------------------------------------------------------
@app.route('/api/system/timezone', methods=['GET'])
def api_system_timezone_get():
    try:
        result = subprocess.run(['timedatectl', 'show', '--property=Timezone'],
                               capture_output=True, text=True, timeout=5)
        tz = result.stdout.strip().split('=')[1] if '=' in result.stdout else 'UTC'
        return jsonify({"ok": True, "timezone": tz})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route('/api/system/timezone', methods=['POST'])
def api_system_timezone_set():
    try:
        data = request.get_json()
        tz   = data.get('timezone', '').strip()
        if not tz:
            return jsonify({"ok": False, "error": "Timezone required"})
        result = subprocess.run(['sudo', 'timedatectl', 'set-timezone', tz],
                               capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return jsonify({"ok": True})
        else:
            return jsonify({"ok": False, "error": result.stderr.strip()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    log.info("BambuHelper Surface RT starting...")
    for p in CONFIG["printers"]:
        log.info(f"  {p['name']} — mode={p.get('mode','lan')} enabled={p.get('enabled', True)}")
        t = threading.Thread(target=mqtt_worker, args=(p,), daemon=True)
        t.start()
    threading.Thread(target=periodic_broadcast, daemon=True).start()
    threading.Thread(target=display_monitor, daemon=True).start()
    log.info("Web server starting on port 5000 (LAN access: %s)", lan_access_enabled())
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
