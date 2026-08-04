# Custom GUI client — architecture & implementation blueprint

Goal: replace `javaws viewer.jnlp` with a Python application that shows the
server's screen live, sends keyboard/mouse, and offers power control and
(optional) virtual media.

## 1. Component architecture

```
┌──────────────────────────── Python app ────────────────────────────┐
│                                                                    │
│  ┌──────────┐   ┌────────────┐   ┌─────────────┐   ┌────────────┐  │
│  │ WebLogin │──▶│ TokenMinter│──▶│ APCPSession │──▶│ FramePump  │  │
│  │ (HTTPS)  │   │ (viewer()  │   │ (TCP 3900,  │   │ (reader    │  │
│  │          │   │  .jnlp)    │   │  handshake, │   │  thread)   │  │
│  └──────────┘   └────────────┘   │  TLS wrap)  │   └─────┬──────┘  │
│                                  └──────┬──────┘         │ tiles   │
│                                         │ input pkts     ▼         │
│                                  ┌──────┴──────┐   ┌────────────┐  │
│                                  │ InputSender │   │ TileDecoder│  │
│                                  │ (kbd/mouse) │   │ (a/a/a/d/e │  │
│                                  └──────▲──────┘   │  rewrite)  │  │
│                                         │          └─────┬──────┘  │
│  ┌──────────────────────────────────────┴──────────────┐ │ frame   │
│  │ GUI (PyQt6 / pygame / tkinter canvas)               │◀┘ buffer  │
│  └─────────────────────────────────────────────────────┘          │
│  ┌─────────────┐  ┌──────────────┐                                │
│  │ PowerControl│  │ VirtualMedia │  (pure HTTP /data?set + .esp)  │
│  └─────────────┘  └──────────────┘                                │
└────────────────────────────────────────────────────────────────────┘
```

## 2. Working code — the parts already proven

### 2.1 Login + token mint (verified live)

```python
import re, ssl, urllib.request, http.cookiejar

IMM = "192.168.0.100"  # <-- your IMM IP

def login(user="USERID", password="PASSW0RD"):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ctx),
        urllib.request.HTTPCookieProcessor(cj))
    req = urllib.request.Request(
        f"https://{IMM}/data/login",
        data=f"user={user}&password={password}&SessionTimeout=600".encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert '"status":"ok"' in opener.open(req).read().decode()
    return opener

def mint_token(opener, client_ip):
    url = (f"https://{IMM}/designs/imm/viewer("
           f"{IMM}@443@{client_ip}@1785523200000@0@1@1@jnlp@0@0@0@0@0@0).jnlp")
    jnlp = opener.open(url).read().decode()
    m = re.search(r"<argument>user=(0x[0-9A-Fa-f]+)</argument>", jnlp)
    if not m:
        raise RuntimeError(f"no token in JNLP: {jnlp[:300]}")
    return int(m.group(1), 16)
```

### 2.2 APCP session-setup packet (bytecode-derived; validate per §4)

```python
import os, socket, struct

def apcp_hello(sock: socket.socket, token: int,
               conn_type=1, subtype=0x0100,
               major=1, minor=2):
    key = os.urandom(20)
    pkt  = b"APCP"
    pkt += struct.pack(">I", 35)         # length (see doc/apcp-protocol.md §2)
    pkt += struct.pack(">H", 0x0100)     # msg type: session request
    pkt += struct.pack(">H", subtype)    # 0x0100 / 0x0102
    pkt += struct.pack(">B", conn_type)  # 1 plain / 3 reconnect / 4 ssl
    pkt += struct.pack(">BB", major, minor)
    pkt += b"\x00"
    pkt += struct.pack(">I", token)      # JNLP user= token
    pkt += struct.pack(">B", len(key)) + key + b"\x00" * (20 - len(key))
    pkt += struct.pack(">B", len(key))
    sock.sendall(pkt)
```

### 2.3 Legacy-TLS context (needed only when IMM negotiates SSL)

```python
import ssl

def legacy_ctx():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.set_ciphers("ALL:@SECLEVEL=0")
    ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    return ctx
```

## 3. What still has to be reverse-engineered (in order)

| # | Item | Where to look |
|---|---|---|
| 1 | Keyboard and mouse-button BEEF message formats | official-client capture while typing and clicking |
| 2 | Additional VID codec variants beyond verified `0x8601` / `0x8605` | packet factory and `com/avocent/kvm/a/*` decoder classes |
| 3 | Redirect-port semantics (when does server send port ≠ 0) | pcap |
| 4 | Virtual media (optional) | skip JNI; drive `/data?set` + `rp_image_upload.esp` over HTTP |

## 4. Validation plan (do this FIRST)

One-time capture of the real client turns all [INFERENCE] into facts:

1. Any machine with Java 8 (a VM is fine): `javaws reference/viewer-sample.jnlp`
   (mint a fresh JNLP first — tokens expire).
2. `sudo tcpdump -i any -w kvm.pcap host <IMM_IP> and port 3900` running
   on this Linux box while the Java client connects, types a few keys, moves the
   mouse, and receives video.
3. Compare first 42 bytes of the capture against `apcp_hello()`; adjust
   conn_type/subtype/length semantics until identical.
4. For TLS: launch with `javaws -J-Djavax.net.debug=ssl,handshake …` to see the
   agreed protocol/cipher, then pin those in `legacy_ctx()`.

## 5. GUI design notes

- **Tile rendering**: implemented in `APCPVideoDecoder`. It keeps a persistent RGB framebuffer, applies differential RGB555 commands, and publishes only the newest completed frame to pygame.
- **Threads**: one reader thread performs blocking `recv`, framing, ACKs, and decode; the pygame loop snapshots only a newer framebuffer generation and uses SDL scaling.
- **Mouse**: verified absolute movement uses BEEF `0x0201` after the secondary logical APCP channel; it wakes a blanked display. Mouse buttons verified: `0x0201` body carries a button mask (`0x0001` left, `0x0002` right, `0x0004` middle), plus a one-time BEEF `0x0403` after the first button event. Keyboard verified: BEEF `0x0200` with state u16 + USB HID usage u16.
- **Status bar**: JNLP `statusbar=un,ip,pwr,fr,kp,bw,led,enc` hints at what the
  client displays: username, IP, power state, framerate, keyboard lock LEDs,
  bandwidth, encryption indicator.
- **Nice extras** (all HTTP, no protocol work): power on/off/cycle buttons,
  event-log viewer, sensor readouts — all available through the web UI endpoints.

## 6. Dependencies

Python ≥ 3.10 stdlib covers login/token/socket/TLS. pygame provides the GUI, input loop, and native SDL surface scaling. Dependencies are managed with `uv`.
