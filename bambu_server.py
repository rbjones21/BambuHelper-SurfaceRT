#!/usr/bin/env python3
"""
BambuHelper Surface RT - MQTT bridge + web dashboard server
Supports both LAN mode (direct printer connection) and
Bambu Cloud mode (via us.mqtt.bambulab.com) per printer.
"""

import json
import os
import re
import shutil
import ssl
import subprocess
import threading
import time
import logging
import urllib.request
import urllib.error
from flask import Flask, render_template, jsonify, request, Response
from flask_socketio import SocketIO
import paho.mqtt.client as mqtt

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config paths
# ---------------------------------------------------------------------------
CONFIG_PATH            = '/etc/bambuhelper/config.json'
KNOWN_GOOD_CONFIG_PATH = '/etc/bambuhelper/config.known-good.json'

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

# ---------------------------------------------------------------------------
# Cloud broker config
# ---------------------------------------------------------------------------
CLOUD_BROKERS = {
    "us": "us.mqtt.bambulab.com",
    "cn": "cn.mqtt.bambulab.com",
    "eu": "us.mqtt.bambulab.com",
}

def get_connection_params(printer_cfg):
    mode = printer_cfg.get("mode", "lan").lower()
    if mode == "cloud":
        region = printer_cfg.get("region", "us")
        host   = CLOUD_BROKERS.get(region, CLOUD_BROKERS["us"])
        user   = f"u_{printer_cfg['bambu_user_id']}"
        pwd    = printer_cfg["bambu_token"]
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
        "nozzle_temp":    0.0,
        "nozzle_target":  0.0,
        "nozzle_temp_l":  None,   # left nozzle — populated if H2D sends separate temps
        "nozzle_temp_r":  None,   # right nozzle
        "nozzle_target_l": None,
        "nozzle_target_r": None,
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
        "spd_lvl":        2,
        "stage":          "",
        "last_update":    0,
        "nozzle_type":    "",
        "nozzle_diameter": "",
        "vir_slots":      [],
        "ams_trays":      [],
        "ams_job_slots":  [],
    }

state_lock      = threading.Lock()
active_clients  = {}  # printer_id -> active mqtt client
printer_states  = {cfg["id"]: default_state(cfg) for cfg in CONFIG["printers"]}
last_payloads   = {}  # printer_id -> last raw print payload (for debug)
last_rich_payloads = {}  # printer_id -> last payload that contained nozzle_temper (for debug)

# ---------------------------------------------------------------------------
# Flask + SocketIO
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.config['SECRET_KEY'] = 'bambuhelper-secret'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

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
            env={'DISPLAY': ':0', 'XAUTHORITY': '/home/rjones/.Xauthority'}
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

def parse_print_message(state, msg):
    p = msg.get('print', {})

    # Store raw payload for debug inspection
    last_payloads[state['id']] = p
    # Also keep the last payload that contained temperature data (not overwritten by heartbeats)
    if 'nozzle_temper' in p:
        last_rich_payloads[state['id']] = p

    if 'nozzle_temper'        in p: state['nozzle_temp']    = round(float(p['nozzle_temper']), 1)
    if 'nozzle_target_temper' in p: state['nozzle_target']  = round(float(p['nozzle_target_temper']), 1)

    # Dual nozzle temps — try known H2D field variants
    for lkey, rkey in [
        ('left_nozzle_temper',  'right_nozzle_temper'),
        ('nozzle_temper_l',     'nozzle_temper_r'),
        ('nozzle_temper0',      'nozzle_temper1'),
        ('nozzle_temp_left',    'nozzle_temp_right'),
    ]:
        if lkey in p or rkey in p:
            if lkey in p: state['nozzle_temp_l'] = round(float(p[lkey]), 1)
            if rkey in p: state['nozzle_temp_r'] = round(float(p[rkey]), 1)
            break
    for lkey, rkey in [
        ('left_nozzle_target_temper',  'right_nozzle_target_temper'),
        ('nozzle_target_temper_l',     'nozzle_target_temper_r'),
        ('nozzle_target_temper0',      'nozzle_target_temper1'),
    ]:
        if lkey in p or rkey in p:
            if lkey in p: state['nozzle_target_l'] = round(float(p[lkey]), 1)
            if rkey in p: state['nozzle_target_r'] = round(float(p[rkey]), 1)
            break

    # Log any unhandled field that looks temperature/nozzle related — helps identify H2D fields
    _known = {
        'nozzle_temper','nozzle_target_temper','bed_temper','bed_target_temper',
        'chamber_temper','nozzle_type','nozzle_diameter',
        'left_nozzle_temper','right_nozzle_temper','nozzle_temper_l','nozzle_temper_r',
        'nozzle_temper0','nozzle_temper1','nozzle_temp_left','nozzle_temp_right',
        'left_nozzle_target_temper','right_nozzle_target_temper',
        'nozzle_target_temper_l','nozzle_target_temper_r',
        'nozzle_target_temper0','nozzle_target_temper1',
    }
    for k, v in p.items():
        if ('nozzle' in k or 'temper' in k) and k not in _known:
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
    if 'nozzle_type'          in p: state['nozzle_type']    = p.get('nozzle_type', '')
    if 'nozzle_diameter'      in p: state['nozzle_diameter'] = p.get('nozzle_diameter', '')

    
# Parse AMS tray data
    if 'ams' in p and isinstance(p['ams'], dict):
        ams_list = p['ams'].get('ams', [])
        trays = []
        for ams_unit in ams_list:
            for tray in ams_unit.get('tray', []):
                color     = tray.get('tray_color', '00000000')
                hex_color = f"#{color[:6]}" if len(color) >= 6 else '#888888'
                trays.append({
                    'id':       f"{ams_unit.get('id','0')}-{tray.get('id','0')}",
                    'color':    hex_color,
                    'type':     tray.get('tray_info_idx', ''),
                    'name':     tray.get('tray_id_name', ''),
                    'remain':   tray.get('remain', -1),
                    'temp':     ams_unit.get('temp', ''),
                    'humidity': ams_unit.get('humidity', ''),
                    'state':    tray.get('state', 0),
                    'active':   tray.get('state') == 24,
                    'in_job':   False,
                })
        if trays:
            state['ams_trays'] = trays
            # Apply any previously stored job slot mapping
            if state.get('ams_job_slots'):
                for tray in state['ams_trays']:
                    tray['in_job'] = tray['id'] in state['ams_job_slots']

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
            })
        state['vir_slots'] = vir

    if 'gcode_state' in p:
        state['gcode_state'] = p['gcode_state']
        state['printing']    = p['gcode_state'] in ('RUNNING', 'PAUSE')

    if 'print_error' in p and p['print_error'] != 0:
        code     = p['print_error']
        msg_text = BAMBU_ERRORS.get(code, f"Error code: {hex(code)}")
        if msg_text not in state['errors']:
            state['errors'].append(msg_text)
            if len(state['errors']) > 5:
                state['errors'].pop(0)

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
            state['hms_errors'] = formatted
        else:
            state['hms_errors'] = []

    if p.get('gcode_state') == 'FINISH':
        state['errors']     = []
        state['hms_errors'] = []
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
    DISPLAY_ENV = {'DISPLAY': ':0', 'XAUTHORITY': '/home/rjones/.Xauthority'}

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
        result = subprocess.run(
            ['nmcli', '-t', 'connection', 'show', 'IoTWLan'],
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
        return jsonify({"ok": True, "method": method, "address": address,
                        "gateway": gateway, "dns": dns})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route('/api/network/ipconfig', methods=['POST'])
def api_network_ipconfig_save():
    try:
        data    = request.get_json()
        ssid    = data.get('ssid', 'IoTWLan')
        method  = data.get('method', 'auto')

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
    # Scalar fields + vir_slot for inspection
    simple = {k: v for k, v in payload.items()
              if not isinstance(v, (dict, list)) or k in ('vir_slot',)}
    return jsonify({"ok": True, "printer_id": printer_id,
                    "from_rich": printer_id in last_rich_payloads, "fields": simple})

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
            printer_states[printer_id]['errors']     = []
            printer_states[printer_id]['hms_errors'] = []
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
    log.info("Web server starting on port 5000 (localhost only)")
    socketio.run(app, host='127.0.0.1', port=5000, debug=False, allow_unsafe_werkzeug=True)
