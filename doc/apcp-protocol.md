# APCP & BEEF — IMM2 KVM Wire Protocol (TCP 3900)

Source of truth: captured wire traffic from the official Java client (`com.avocent.ibmc.kvm.Main`)
under `xvfb-run` through `tools/mitm_proxy.py` against live IBM System x3650 M4 IMM2.

All binary integers are Big-Endian (`struct.pack(">...")`).

## 1. Complete Session Sequence

```
Client                                                  IMM2 (port 3900)
  |── 1. 68-byte APCP v2.40 Handshake Request ──────────▶|
  |◀── 2. 68-byte APCP Handshake Response (Con ID != 0) ──|
  |                                                      |
  |── 3. 217-byte BEEF Session Start Packet (w/ Token) ─▶|
  |◀── 4. 131-byte BEEF Response & Capabilities ─────────|
  |                                                      |
  |── 5. Session Init Sequence:                          |
  |        - APCP 12-byte Keepalive (0x0400) ───────────▶|
  |        - BEEF 16-byte Packet #1 ────────────────────▶|
  |        - BEEF 16-byte Packet #2 ────────────────────▶|
  |        - BEEF 16-byte Packet #3 ────────────────────▶|
  |                                                      |
  |◀══ 6. Server Starts Video Tile Stream (VID\x00) ══════|
  |── 7. Client Frame ACK (16-byte BEEF) ───────────────▶| (on each tile)
  |── 8. Periodic APCP Keepalive (12-byte 0x0400) ──────▶| (every 2 sec)
```

## 2. Packet Specifications

### 2.1 68-byte APCP Handshake Request (Client → Server)

| Offset | Length | Field | Value / Description |
|---|---|---|---|
| 0 | 4 | Magic | ASCII `APCP` (`41 50 43 50`) |
| 4 | 4 | Body Length | `0x00000044` (68 decimal) |
| 8 | 2 | Type | `0x0100` |
| 10 | 2 | Subtype | `0x0102` |
| 12 | 1 | Mode | `3` (Combined KVM + Virtual Media) |
| 13 | 1 | Major Version | `2` |
| 14 | 1 | Minor Version | `40` (v2.40) |
| 15 | 1 | Reserved | `0` |
| 16 | 4 | Constant ID | `0x00000605` |
| 20 | 1 | Key Length | `32` (`0x20`) |
| 21 | 32 | Client Key | 32 random bytes (`os.urandom(32)`) |
| 53 | 1 | Suffix | `32` (`0x20`) |
| 54 | 15 | Extended Tail | `00 00 00 00 b4 00 1e 00 00 00 00 00 00 00 00` |

### 2.2 68-byte APCP Handshake Response (Server → Client)

| Offset | Length | Field | Value / Description |
|---|---|---|---|
| 0 | 4 | Magic | ASCII `APCP` (`41 50 43 50`) |
| 4 | 4 | Body Length | `0x00000044` (68 decimal) |
| 8 | 2 | Status | `0x8100` (Session Accepted) |
| 10 | 2 | Subtype | `0x0102` |
| 12 | 2 | Capabilities | `0x0228` (v2.40 caps) |
| 14 | 4 | **Con ID** | **Non-zero** (e.g. `0x00000601` = Accepted; `0x00000000` = Session Slot Busy / Rejected) |
| 18 | 50 | Tail Padding | 50 bytes zero padding |

### 2.3 217-byte BEEF Session Start Packet (Client → Server)

| Offset | Length | Field | Value / Description |
|---|---|---|---|
| 0 | 4 | Magic | ASCII `BEEF` (`42 45 45 46`) |
| 4 | 2 | Message Type | `0x0102` (uint16 BE) |
| 6 | 2 | Message Length | `0x00D9` (uint16 BE = 217 decimal) |
| 8 | Variable | Token String | `\n` + `0xXXXXXXXX` (ASCII JNLP user token) |
| End - 7 | 7 | Tail | `3c 00 00`, three random bytes, then `00` |

### 2.4 Client Frame ACK Packet (Client → Server: 16 bytes)

Sent by the client immediately whenever a video tile packet arrives:

```
BEEF\x00\x00\x00\x10\x01\x00\x00\x00\x00\x00\x00\x00
```

### 2.5 VID Video Packets (Server → Client)

All video packets use the common 8-byte envelope:

| Offset | Length | Field | Value / Description |
|---|---|---|---|
| 0 | 4 | Magic | `VID\x00` |
| 4 | 2 | Message Type | `0x8601` pixel stream or `0x8605` video-mode notification |
| 6 | 2 | Total Length | uint16 BE, including the 8-byte envelope |

`0x8601` body layout:

| Offset | Length | Field | Value / Description |
|---|---|---|---|
| 8 | 4 | Marker | `DE AD BE EF` |
| 12 | 2 | Height | uint16 BE |
| 14 | 2 | Width | uint16 BE |
| 16 | 1 | Flags | bit 0 = frame start, bit 1 = frame end |
| 17 | 3 | Reserved | zero in captures |
| 20 | Variable | Payload | differential RGB555 command stream |

The payload updates a persistent framebuffer. Opcodes are selected by the command byte's top three bits: `000` skips unchanged pixels, `001` repeats the previous pixel, `010` copies from the previous scanline, and `011` emits a two-color pattern. Bytes with bit 7 set encode one RGB555 literal using the next byte. Skip/repeat/copy lengths use 5-bit continuation groups.

`0x8605` is a 20-byte video-mode notification. Its body carries height at offsets 10–11 and width at 12–13. The reference client clears the framebuffer when it arrives while waiting for subsequent pixel data.

### 2.6 Mouse Input Channel

After the primary session-init messages, the official client sends a second 68-byte APCP request on the same TCP stream. It uses mode `0x9F`, version `2.37`, connection ID `4`, a 32-byte random key, and the normal 15-byte APCP tail. The server response carries connection ID `4`; the client then sends:

```
BEEF\x03\x01\x00\x10\x00\x00\x00\x00\x00\x00\x00\x00
```

Mouse movement is enabled with captured 16-byte BEEF packets `0x0208`, `0x0204`, and `0x0202`. Absolute movement and button state use message `0x0201`:

| Offset | Length | Field |
|---|---|---|
| 0 | 4 | `BEEF` |
| 4 | 2 | `0x0201` |
| 6 | 2 | `0x0010` |
| 8 | 2 | button mask (`0x0001` left, `0x0002` right, `0x0004` middle; `0` when released) |
| 10 | 2 | absolute X in remote framebuffer pixels |
| 12 | 2 | absolute Y in remote framebuffer pixels |
| 14 | 2 | zero |

A click is a press packet (mask set at the position) followed by a release packet (mask cleared at the same position); dragging holds the mask across the intervening moves. Verified with controlled left/right/middle clicks of the official client (Java Robot) through the proxy: `0201 [0001] x y 0000` + `0201 [0000] x y 0000` for a left click, `0002`/`0004` for right/middle.

After the first button event the official client sends one 16-byte BEEF `0x0403` with body `01 00 00 00 00 00 00 00`; the server answers with BEEF `0x8204` (body `0f 01 00 00 00 00 00 00`), the mouse-state message parsed by `com.avocent.c.c.n` (flags byte, then two booleans).

### 2.7 Keyboard Input Channel

Keyboard events use 16-byte BEEF `0x0200` packets with USB HID usage IDs:

| Offset | Length | Field |
|---|---|---|
| 0 | 4 | `BEEF` |
| 4 | 2 | `0x0200` |
| 6 | 2 | `0x0010` |
| 8 | 2 | state (`0x0000` down, `0x0001` up) |
| 10 | 2 | USB HID keyboard usage ID (a=`0x04`, Enter=`0x28`, Backspace=`0x2A`, F1=`0x3A`, left shift=`0xE1`, left ctrl=`0xE0`) |
| 12 | 2 | zero |
| 14 | 2 | zero |

Modifiers (Shift/Ctrl/Alt) are sent as their own `0x0200` events before the key. Verified with controlled keystrokes of the official client (Java Robot) through the proxy: `a` = `0200 0010 0000 0004 0000 0000` down + `0200 0010 0001 0004 0000 0000` up; `Shift+A` = `0000 00e1`, `0000 0004`, `0001 0004`, `0001 00e1`.

This movement wakes a host display that is only emitting `0x8605` mode notifications; `0x8601` pixel frames follow. The earlier guessed 26-byte keyboard and 32-byte mouse buffers are not wire packets and reset the IMM session when written directly.
