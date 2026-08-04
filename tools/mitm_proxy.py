"""
APCP TCP Logging Proxy

Intercepts TCP traffic on 127.0.0.1:3900 and forwards to the IMM host:3900,
logging all binary packets in hex and ASCII. The target host comes from
config.json ("host" key), overridable with the IMM_HOST environment variable.
"""

import os
import socket
import sys
import threading
import time

# Allow running as `python tools/mitm_proxy.py` from anywhere: the script's
# own directory (tools/) is on sys.path, not the repo root that holds src/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.imm_config import load as load_config


def resolve_target_host() -> str:
    """IMM host from IMM_HOST env var, else config.json 'host'; error if neither."""
    env_host = os.environ.get("IMM_HOST")
    if env_host:
        return env_host
    host = load_config().get("host")
    if not host:
        raise SystemExit(
            "No IMM host configured: set IMM_HOST or add a 'host' key to config.json"
        )
    return host


TARGET_HOST = resolve_target_host()
TARGET_PORT = 3900
LISTEN_PORT = 3900

LOG_FILE = "/tmp/apcp_wire_log.txt"


def log(msg: str):
    timestamp = time.strftime("%H:%M:%S")
    formatted = f"[{timestamp}] {msg}"
    print(formatted)
    with open(LOG_FILE, "a") as f:
        f.write(formatted + "\n")


def forward(src: socket.socket, dst: socket.socket, direction: str):
    try:
        while True:
            data = src.recv(4096)
            if not data:
                log(f"[{direction}] Connection closed (0 bytes)")
                break
            log(f"[{direction}] {len(data)} bytes:\n  HEX: {data.hex()}\n  RAW: {data!r}")
            dst.sendall(data)
    except Exception as e:
        log(f"[{direction}] Error: {e}")
    finally:
        try:
            src.close()
        except Exception:
            pass
        try:
            dst.close()
        except Exception:
            pass


def handle_client(client_sock: socket.socket, addr):
    log(f"[PROXY] Accepted connection from {addr}")
    try:
        server_sock = socket.create_connection((TARGET_HOST, TARGET_PORT), timeout=10)
        log(f"[PROXY] Connected to target {TARGET_HOST}:{TARGET_PORT}")

        t1 = threading.Thread(target=forward, args=(client_sock, server_sock, "CLIENT -> SERVER"), daemon=True)
        t2 = threading.Thread(target=forward, args=(server_sock, client_sock, "SERVER -> CLIENT"), daemon=True)

        t1.start()
        t2.start()
        t1.join()
        t2.join()
    except Exception as e:
        log(f"[PROXY] Connection handling failed: {e}")


def run_proxy():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", LISTEN_PORT))
    server.listen(5)
    log(f"[PROXY] Listening on 127.0.0.1:{LISTEN_PORT} -> {TARGET_HOST}:{TARGET_PORT}")

    while True:
        client_sock, addr = server.accept()
        t = threading.Thread(target=handle_client, args=(client_sock, addr), daemon=True)
        t.start()


if __name__ == "__main__":
    run_proxy()
