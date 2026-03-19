#!/usr/bin/env python3
"""
BambuHelper Surface RT - MQTT bridge + web dashboard server
Supports both LAN mode (direct printer connection) and
Bambu Cloud mode (via us.mqtt.bambulab.com) per printer.
"""

import json
import ssl
import threading
import time
import logging
from flask import Flask, render_template, jsonify, request, Response
from flask_socketio import SocketIO
import paho.mqtt.client as mqtt

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load config
# ---------------------------------------------------------------------------
CONFIG_PATH = '/etc/bambuhelper/config.json'

def load_config():
    try:
        with open(CONFIG_PATH, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        log.warning(f"Config not found at {CONFIG_PATH}, using defaults")
        return {
            "printers": [
                {
                    "id": "printer1",
                    "name": "H2D",
                    "mode": "lan",
                    "ip": "192.168.1.100",
                    "serial": "YOUR_SERIAL_1",
                    "access_code": "YOUR_CODE_1",
                    "enabled": False
                },
                {
                    "id": "printer2",
                    "name": "H2C",
                    "mode": "lan",
                    "ip": "192.168.1.101",
                    "serial": "YOUR_SERIAL_2",
                    "access_code": "YOUR_CODE_2",
                    "enabled": False
                }
            ]
        }

CONFIG = load_config()

# ---------------------------------------------------------------------------
# Cloud broker config
# ---------------------------------------------------------------------------
CLOUD_BROKERS = {
    "us": "us.mqtt.bambulab.com",
    "cn": "cn.mqtt.bambulab.com",
    "eu": "us.mqtt.bambulab.com",   # EU routes via US broker
}

def get_connection_params(printer_cfg):
    """
    Return (host, port, username, password) based on printer mode.

    LAN mode:
      host     = printer IP
      username = 'bblp'
      password = LAN access code (8 chars from printer screen)

    Cloud mode:
      host     = us.mqtt.bambulab.com (or cn)
      username = 'u_{bambu_user_id}'
      password = Bambu cloud token (from MakerWorld cookies)
    """
    mode = printer_cfg.get("mode", "lan").lower()

    if mode == "cloud":
        region = printer_cfg.get("region", "us")
        host   = CLOUD_BROKERS.get(region, CLOUD_BROKERS["us"])
        user   = f"u_{printer_cfg['bambu_user_id']}"
        pwd    = printer_cfg["bambu_token"]
    else:
        # LAN mode (default)
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
        "last_update":    0,
    }

printer_states = {cfg["id"]: default_state(cfg) for cfg in CONFIG["printers"]}
state_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Flask + SocketIO
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.config['SECRET_KEY'] = 'bambuhelper-secret'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

@app.route('/')
def index():
    return render_template('dashboard.html')

@app.route('/settings')
def settings():
    return render_template('settings.html')

@app.route('/api/state')
def api_state():
    with state_lock:
        return jsonify(list(printer_states.values()))

@app.route('/api/config')
def api_config():
    # Return full config including credentials (settings page needs them)
    return jsonify(CONFIG)

@app.route('/api/config/save', methods=['POST'])
def api_config_save():
    global CONFIG, printer_states
    try:
        new_config = request.get_json()
        if not new_config or 'printers' not in new_config:
            return jsonify({"ok": False, "error": "Invalid config format"})

        # Write to disk
        import os
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, 'w') as f:
            json.dump(new_config, f, indent=2)

        # Reload in memory
        CONFIG = new_config

        # Reset printer states for any changed printers
        with state_lock:
            for cfg in CONFIG["printers"]:
                pid = cfg["id"]
                if pid not in printer_states:
                    printer_states[pid] = default_state(cfg)
                else:
                    # Preserve live connection state, update config fields
                    printer_states[pid]['name']    = cfg.get('name', pid)
                    printer_states[pid]['mode']    = cfg.get('mode', 'lan')
                    printer_states[pid]['enabled'] = cfg.get('enabled', True)

        # Restart MQTT workers in background
        def restart_workers():
            time.sleep(1)
            for cfg in CONFIG["printers"]:
                t = threading.Thread(target=mqtt_worker, args=(cfg,), daemon=True)
                t.start()

        threading.Thread(target=restart_workers, daemon=True).start()

        log.info("Config saved and connections restarting")
        return jsonify({"ok": True})

    except Exception as e:
        log.error(f"Config save error: {e}")
        return jsonify({"ok": False, "error": str(e)})

def broadcast_state():
    with state_lock:
        data = list(printer_states.values())
    socketio.emit('state_update', data)

@app.route('/api/display')
def api_display():
    """Return display settings including colors."""
    return jsonify(CONFIG.get('display', {}))

@app.route('/api/thumbnail/<printer_id>')
def api_thumbnail(printer_id):
    """Fetch and proxy the current print thumbnail from Bambu cloud task API."""
    try:
        printer_cfg = next((p for p in CONFIG["printers"] if p["id"] == printer_id), None)
        if not printer_cfg or printer_cfg.get("mode") != "cloud":
            return jsonify({"ok": False, "error": "Cloud mode only"})

        token   = printer_cfg.get("bambu_token", "")
        serial  = printer_cfg.get("serial", "")

        import urllib.request, urllib.error
        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": "bambuhelper-rt/1.0"
        }

        # Fetch recent task list — cover field contains thumbnail URL
        url = f"https://api.bambulab.com/v1/iot-service/api/user/task?deviceId={serial}&limit=3"
        req = urllib.request.Request(url, headers=headers)

        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            log.warning(f"Thumbnail API HTTP error {e.code} for {printer_id}")
            return jsonify({"ok": False, "error": f"HTTP {e.code}"})

        # Find the most recent task with a cover image
        tasks = data.get("hits", data.get("data", []))
        cover_url = None
        for task in tasks:
            if task.get("cover"):
                cover_url = task["cover"]
                break

        if not cover_url:
            log.info(f"No thumbnail cover found for {printer_id}")
            return jsonify({"ok": False, "error": "No thumbnail available"})

        # Proxy the image
        img_req = urllib.request.Request(cover_url, headers={"User-Agent": "bambuhelper-rt/1.0"})
        with urllib.request.urlopen(img_req, timeout=8) as img_resp:
            img_data     = img_resp.read()
            content_type = img_resp.headers.get("Content-Type", "image/jpeg")

        return Response(img_data, mimetype=content_type)

    except Exception as e:
        log.warning(f"Thumbnail fetch failed for {printer_id}: {e}")
        return jsonify({"ok": False, "error": str(e)})

@app.route('/api/network')
def api_network():
    """Return Surface RT WiFi signal strength and connection quality."""
    try:
        import subprocess, re
        result = subprocess.run(['iwconfig', 'mlan0'], capture_output=True, text=True, timeout=3)
        output = result.stdout
        signal, ssid = None, None
        for line in output.splitlines():
            if 'Signal level' in line:
                m = re.search(r'Signal level=(-\d+)', line)
                if m: signal = int(m.group(1))
            if 'ESSID' in line:
                m = re.search(r'ESSID:"([^"]+)"', line)
                if m: ssid = m.group(1)
        quality = 'good' if signal and signal > -60 else 'fair' if signal and signal > -75 else 'poor'
        return jsonify({"ok": True, "signal": signal, "ssid": ssid, "quality": quality})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route('/api/display/rotate', methods=['POST'])
def api_display_rotate():
    try:
        data     = request.get_json()
        rotation = data.get('rotation', 'normal')
        import subprocess
        subprocess.Popen(
            ['xrandr', '--output', 'DSI-1', '--rotate', rotation],
            env={'DISPLAY': ':0', 'XAUTHORITY': '/home/rjones/.Xauthority'}
        )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route('/api/printer/control', methods=['POST'])
def api_printer_control():
    try:
        data       = request.get_json()
        printer_id = data.get('printer_id')
        command    = data.get('command')  # pause, resume, stop

        if command not in ('pause', 'resume', 'stop'):
            return jsonify({"ok": False, "error": "Invalid command"})

        # Find the printer config
        printer_cfg = next((p for p in CONFIG["printers"] if p["id"] == printer_id), None)
        if not printer_cfg:
            return jsonify({"ok": False, "error": "Printer not found"})

        serial  = printer_cfg["serial"]
        payload = json.dumps({
            "print": {
                "sequence_id": "0",
                "command": command
            }
        })

        # Find the active MQTT client for this printer and publish
        # We publish via a fresh one-shot client to keep it simple
        host, port, username, password = get_connection_params(printer_cfg)

        import threading
        def publish():
            import paho.mqtt.publish as publish_mqtt
            import ssl
            tls = ssl.create_default_context()
            tls.check_hostname = False
            tls.verify_mode    = ssl.CERT_NONE
            publish_mqtt.single(
                topic   = f"device/{serial}/request",
                payload = payload,
                hostname   = host,
                port       = port,
                auth       = {"username": username, "password": password},
                tls        = {"context": tls},
                protocol   = mqtt.MQTTv311,
                qos        = 1
            )
            log.info(f"[{printer_id}] Sent command: {command}")

        threading.Thread(target=publish, daemon=True).start()
        return jsonify({"ok": True})

    except Exception as e:
        log.error(f"Control error: {e}")
        return jsonify({"ok": False, "error": str(e)})

# ---------------------------------------------------------------------------
# MQTT message parser — identical for LAN and cloud (same Bambu protocol)
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

def parse_print_message(state, msg):
    p = msg.get('print', {})

    if 'nozzle_temper'        in p: state['nozzle_temp']    = round(float(p['nozzle_temper']), 1)
    if 'nozzle_target_temper' in p: state['nozzle_target']  = round(float(p['nozzle_target_temper']), 1)
    if 'bed_temper'           in p: state['bed_temp']        = round(float(p['bed_temper']), 1)
    if 'bed_target_temper'    in p: state['bed_target']      = round(float(p['bed_target_temper']), 1)
    if 'chamber_temper'       in p: state['chamber_temp']    = round(float(p['chamber_temper']), 1)

    # Fans: Bambu uses 0-15 scale, convert to 0-100%
    if 'cooling_fan_speed' in p: state['fan_part']    = round((int(p['cooling_fan_speed']) / 15) * 100)
    if 'big_fan1_speed'    in p: state['fan_aux']     = round((int(p['big_fan1_speed'])    / 15) * 100)
    if 'big_fan2_speed'    in p: state['fan_chamber'] = round((int(p['big_fan2_speed'])    / 15) * 100)

    if 'mc_percent'       in p: state['progress']       = int(p['mc_percent'])
    if 'layer_num'        in p: state['layer_current']  = int(p['layer_num'])
    if 'total_layer_num'  in p: state['layer_total']    = int(p['total_layer_num'])
    if 'mc_remaining_time'in p: state['time_remaining'] = int(p['mc_remaining_time'])
    if 'subtask_name'     in p: state['print_name']     = p.get('subtask_name', '')
    if 'spd_lvl'          in p: state['spd_lvl']         = int(p['spd_lvl'])

    if 'gcode_state' in p:
        state['gcode_state'] = p['gcode_state']
        state['printing']    = p['gcode_state'] in ('RUNNING', 'PAUSE')

    if 'print_error' in p and p['print_error'] != 0:
        code = p['print_error']
        msg_text = BAMBU_ERRORS.get(code, f"Error code: {hex(code)}")
        if msg_text not in state['errors']:
            state['errors'].append(msg_text)
            if len(state['errors']) > 5:
                state['errors'].pop(0)

    # HMS errors — array of {attr, code} objects from printer
    if 'hms' in p:
        hms_list = p['hms']
        if isinstance(hms_list, list) and len(hms_list) > 0:
            formatted = []
            for h in hms_list:
                attr = h.get('attr', 0)
                code = h.get('code', 0)
                # Format as XXXX-XXXX-XXXX-XXXX for wiki URL
                a1 = (attr >> 16) & 0xFFFF
                a2 = attr & 0xFFFF
                c1 = (code >> 16) & 0xFFFF
                c2 = code & 0xFFFF
                formatted.append(f"{a1:04X}-{a2:04X}-{c1:04X}-{c2:04X}")
            state['hms_errors'] = formatted
        else:
            state['hms_errors'] = []

    if p.get('gcode_state') == 'FINISH':
        state['errors'] = []

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

            # Subscribe to printer report topic
            topic = f"device/{serial}/report"
            client.subscribe(topic)
            log.info(f"[{printer_id}] Subscribed to {topic}")

            # Request immediate full status dump
            pushall = json.dumps({
                "pushing": {"sequence_id": "0", "command": "pushall"}
            })
            client.publish(f"device/{serial}/request", pushall)
            broadcast_state()
        else:
            reasons = {
                1: "Bad protocol version",
                2: "Client ID rejected",
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

    # TLS — Bambu uses self-signed certs on LAN; cloud uses valid certs
    # We disable verification for both to keep things simple
    tls_ctx = ssl.create_default_context()
    tls_ctx.check_hostname = False
    tls_ctx.verify_mode    = ssl.CERT_NONE
    client.tls_set_context(tls_ctx)

    client.on_connect    = on_connect
    client.on_disconnect = on_disconnect
    client.on_message    = on_message

    return client, host, port

# ---------------------------------------------------------------------------
# MQTT worker — one per printer, auto-reconnects with exponential backoff
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
            retry_delay = 5  # reset on success

            # Health monitor — reconnect if no messages for 60s
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
# Periodic broadcast — keeps browser in sync even with no printer messages
# ---------------------------------------------------------------------------
def periodic_broadcast():
    while True:
        time.sleep(5)
        broadcast_state()

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    log.info("BambuHelper Surface RT starting...")
    for p in CONFIG["printers"]:
        log.info(f"  {p['name']} — mode={p.get('mode','lan')} "
                 f"enabled={p.get('enabled', True)}")
        t = threading.Thread(target=mqtt_worker, args=(p,), daemon=True)
        t.start()

    threading.Thread(target=periodic_broadcast, daemon=True).start()

    log.info("Web server starting on port 5000")
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
