"""
APCP Protocol Client & Stream Processing Module (TCP Port 3900)

Implements the verified Avocent Proprietary Control Protocol (APCP v2.40) handshake,
Con ID session validation, 217-byte BEEF session start, frame ACKs, keyboard/mouse encoders,
and 16-bit header stream parsing for BEEF / VID streams.
"""

import os
import queue
import socket
import struct
import threading
import time
from typing import Optional, Tuple


class APCPVideoDecoder:
    """Decode IMM2 VID packets into an RGB framebuffer."""

    HEADER_SIZE = 20
    MAX_DIMENSION = 4096

    def __init__(self):
        self.width = 0
        self.height = 0
        self.framebuffer = bytearray()
        self.frame_generation = 0
        self.last_frame_kind = ""
        self._pending = bytearray()
        self._assembling = False

    def feed(self, packet: bytes) -> bool:
        """Consume one VID packet. Return True when a complete frame is decoded."""
        if len(packet) < self.HEADER_SIZE:
            raise ValueError(f"short VID packet: {len(packet)} bytes")
        if packet[:4] != b"VID\x00":
            raise ValueError(f"invalid VID magic: {packet[:4]!r}")
        message_type = struct.unpack(">H", packet[4:6])[0]
        declared_length = struct.unpack(">H", packet[6:8])[0]
        if declared_length != len(packet):
            raise ValueError(
                f"VID length mismatch: header={declared_length}, actual={len(packet)}"
            )

        if message_type == 0x8605:
            height, width = struct.unpack(">HH", packet[10:14])
            if not (
                0 < width <= self.MAX_DIMENSION and 0 < height <= self.MAX_DIMENSION
            ):
                raise ValueError(f"invalid video mode dimensions: {width}x{height}")
            self.width = width
            self.height = height
            self.framebuffer = bytearray(width * height * 3)
            self._pending.clear()
            self._assembling = False
            self.last_frame_kind = "mode"
            self.frame_generation += 1
            return True

        if message_type != 0x8601:
            raise ValueError(f"unsupported VID type: 0x{message_type:04x}")
        if packet[8:12] != b"\xde\xad\xbe\xef":
            raise ValueError("missing VID DEADBEEF marker")

        height, width = struct.unpack(">HH", packet[12:16])
        if not (0 < width <= self.MAX_DIMENSION and 0 < height <= self.MAX_DIMENSION):
            raise ValueError(f"invalid VID dimensions: {width}x{height}")

        flags = packet[16]
        starts_frame = bool(flags & 0x01)
        ends_frame = bool(flags & 0x02)

        if starts_frame:
            if (width, height) != (self.width, self.height):
                self.width = width
                self.height = height
                self.framebuffer = bytearray(width * height * 3)
            self._pending.clear()
            self._assembling = True
        elif not self._assembling:
            raise ValueError("VID continuation arrived before frame start")
        elif (width, height) != (self.width, self.height):
            raise ValueError(
                f"VID dimensions changed mid-frame: {self.width}x{self.height} -> "
                f"{width}x{height}"
            )

        self._pending.extend(packet[self.HEADER_SIZE :])
        if not ends_frame:
            return False

        self._decode(self._pending)
        self._pending.clear()
        self._assembling = False
        self.last_frame_kind = "pixels"
        self.frame_generation += 1
        return True

    def _decode(self, data: bytearray):
        fb = self.framebuffer
        span = self.width * self.height
        n = len(data)
        cursor = 0
        offset = 0
        # Lazy run tracker for two-color patterns: the decode cursor only moves
        # forward and every write happens at the cursor, so framebuffer pixels
        # behind it never change within a frame. Pixels in [track_pos, cursor)
        # are classified at most once per frame, keeping pattern color lookups
        # amortized O(1); rescanning the current run per 0x60 command is
        # O(patterns * run length) and stalls for seconds on long solid runs.
        track_pos = 0
        run_color = -1
        run_start = 0
        prev_run_color = -1

        while offset < n:
            command = data[offset]
            offset += 1
            opcode = command & 0xE0

            if opcode == 0x60:
                if cursor == 0:
                    previous = 0
                    alternate = 0
                elif track_pos == 0:
                    # First pattern this frame: one backward scan finds the run
                    # containing the previous pixel and the color before it.
                    o = (cursor - 1) * 3
                    previous = (fb[o] << 16) | (fb[o + 1] << 8) | fb[o + 2]
                    alternate = previous
                    scan = cursor - 1
                    while scan >= 0:
                        o = scan * 3
                        candidate = (fb[o] << 16) | (fb[o + 1] << 8) | fb[o + 2]
                        if candidate != previous:
                            alternate = candidate
                            break
                        scan -= 1
                    run_color = previous
                    prev_run_color = alternate
                    run_start = scan + 1
                    track_pos = cursor
                else:
                    while track_pos < cursor:
                        o = track_pos * 3
                        c = (fb[o] << 16) | (fb[o + 1] << 8) | fb[o + 2]
                        if c != run_color:
                            prev_run_color = run_color
                            run_color = c
                            run_start = track_pos
                        track_pos += 1
                    previous = run_color
                    alternate = prev_run_color if run_start > 0 else previous

                total = 4
                masks = 0
                if command & 0x10:
                    while True:
                        if offset + masks >= n:
                            raise ValueError("truncated two-color pattern")
                        mask = data[offset + masks]
                        masks += 1
                        total += 7
                        if not mask & 0x80:
                            break
                if cursor + total > span:
                    raise ValueError(
                        f"VID decode overflow: cursor={cursor}, count={total}, "
                        f"frame={self.width}x{self.height}"
                    )
                prev_px = (
                    (previous >> 16) & 0xFF,
                    (previous >> 8) & 0xFF,
                    previous & 0xFF,
                )
                alt_px = (
                    (alternate >> 16) & 0xFF,
                    (alternate >> 8) & 0xFF,
                    alternate & 0xFF,
                )
                o = cursor * 3
                for bit in (0x08, 0x04, 0x02, 0x01):
                    px = alt_px if command & bit else prev_px
                    fb[o] = px[0]
                    fb[o + 1] = px[1]
                    fb[o + 2] = px[2]
                    o += 3
                cursor += 4
                for _ in range(masks):
                    mask = data[offset]
                    offset += 1
                    for bit in (0x40, 0x20, 0x10, 0x08, 0x04, 0x02, 0x01):
                        px = alt_px if mask & bit else prev_px
                        fb[o] = px[0]
                        fb[o + 1] = px[1]
                        fb[o + 2] = px[2]
                        o += 3
                    cursor += 7
                continue

            if opcode in (0x00, 0x20, 0x40):
                count, offset = self._decode_run_length(data, offset, command, opcode)
                if opcode == 0x00:
                    self._ensure_span(cursor, count)
                    cursor += count
                elif opcode == 0x20:
                    cursor = self._fill(cursor, count, self._read_pixel(cursor - 1))
                else:
                    cursor = self._copy_previous_line(cursor, count)
                continue

            if offset >= n:
                raise ValueError("truncated RGB555 literal")
            while True:
                rgb555 = ((command & 0x7F) << 8) | data[offset]
                offset += 1
                if cursor >= span:
                    raise ValueError(
                        f"VID decode overflow: cursor={cursor}, count=1, "
                        f"frame={self.width}x{self.height}"
                    )
                o = cursor * 3
                fb[o] = (rgb555 >> 7) & 0xF8
                fb[o + 1] = (rgb555 >> 2) & 0xF8
                fb[o + 2] = (rgb555 & 0x1F) << 3
                cursor += 1
                if offset + 1 >= n or data[offset] < 0x80:
                    break
                command = data[offset]
                offset += 1

    @staticmethod
    def _decode_run_length(
        data: bytearray, offset: int, command: int, opcode: int
    ) -> Tuple[int, int]:
        count = command & 0x1F
        shift = 5
        while shift <= 20 and offset < len(data) and data[offset] & 0xE0 == opcode:
            count |= (data[offset] & 0x1F) << shift
            offset += 1
            shift += 5
        return count, offset

    def _copy_previous_line(self, cursor: int, count: int) -> int:
        self._ensure_span(cursor, count)
        source = cursor - self.width
        if source < 0:
            return cursor + count

        remaining = count
        while remaining:
            chunk = min(remaining, cursor - source)
            source_start = source * 3
            target_start = cursor * 3
            byte_count = chunk * 3
            self.framebuffer[target_start : target_start + byte_count] = (
                self.framebuffer[source_start : source_start + byte_count]
            )
            source += chunk
            cursor += chunk
            remaining -= chunk
        return cursor

    def _fill(self, cursor: int, count: int, color: int) -> int:
        self._ensure_span(cursor, count)
        pixel = bytes(((color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF))
        start = cursor * 3
        end = (cursor + count) * 3
        self.framebuffer[start:end] = pixel * count
        return cursor + count

    def _read_pixel(self, index: int) -> int:
        if index < 0 or index >= self.width * self.height:
            return 0
        offset = index * 3
        return (
            (self.framebuffer[offset] << 16)
            | (self.framebuffer[offset + 1] << 8)
            | self.framebuffer[offset + 2]
        )

    def _ensure_span(self, cursor: int, count: int):
        if count < 0 or cursor < 0 or cursor + count > self.width * self.height:
            raise ValueError(
                f"VID decode overflow: cursor={cursor}, count={count}, "
                f"frame={self.width}x{self.height}"
            )


class APCPClient:
    def __init__(self, host: str, port: int = 3900):
        self.host = host
        self.port = port
        self.sock: Optional[socket.socket] = None
        self.running = False
        self.keepalive_thread: Optional[threading.Thread] = None
        self.reader_thread: Optional[threading.Thread] = None
        self.frame_queue = queue.Queue(maxsize=50)
        self.video_decoder = APCPVideoDecoder()
        self.video_lock = threading.Lock()
        self.send_lock = threading.Lock()
        self.secondary_ready = threading.Event()
        self.mouse_initialized = False
        self.button_engaged = False
        self.stream_error: Optional[str] = None
        self.video_error: Optional[str] = None
        self.dropped_frames = 0
        self.session_id = 0

    def build_handshake_packet(self) -> bytes:
        """
        Build the verified 68-byte APCP v2.40 session-setup request packet.
        """
        client_key = os.urandom(32)
        pkt = bytearray()
        pkt += b"APCP"  # 4 bytes magic
        pkt += struct.pack(">I", 0x44)  # 4 bytes (0x00000044 = 68 decimal)
        pkt += struct.pack(">H", 0x0100)  # 2 bytes type
        pkt += struct.pack(">H", 0x0102)  # 2 bytes subtype
        pkt += struct.pack(">B", 3)  # 1 byte mode
        pkt += struct.pack(">B", 2)  # 1 byte major ver 2
        pkt += struct.pack(">B", 40)  # 1 byte minor ver 40 (v2.40)
        pkt += struct.pack(">B", 0)  # 1 byte reserved
        pkt += struct.pack(">I", 0x0605)  # 4 bytes constant 0x0605
        pkt += struct.pack(">B", 0x20)  # 1 byte key len (32)
        pkt += client_key  # 32 random bytes
        pkt += bytes.fromhex(
            "00000000b4001e0000000000000000"
        )  # 15 bytes extended tail (EXACT 68 BYTES)
        return bytes(pkt)

    def build_secondary_handshake_packet(self) -> bytes:
        """Build the official logical input-channel APCP request."""
        client_key = os.urandom(32)
        pkt = bytearray()
        pkt += b"APCP"
        pkt += struct.pack(">I", 0x44)
        pkt += struct.pack(">H", 0x0100)
        pkt += struct.pack(">H", 0x0102)
        pkt += struct.pack(">B", 0x9F)
        pkt += struct.pack(">B", 2)
        pkt += struct.pack(">B", 37)
        pkt += struct.pack(">B", 0)
        pkt += struct.pack(">I", 4)
        pkt += struct.pack(">B", 0x20)
        pkt += client_key
        pkt += bytes.fromhex("00000000b4001e0000000000000000")
        return bytes(pkt)

    def build_session_start_packet(self, token: int) -> bytes:
        """
        Build the verified 217-byte BEEF session start packet carrying token string.
        - Magic: b"BEEF" (4 bytes)
        - Type: 0x0102 (2 bytes at 4:6)
        - Length: 0x00D9 (2 bytes at 6:8 = 217 decimal)
        """
        token_hex = hex(token)
        pkt = bytearray(217)
        pkt[:4] = b"BEEF"
        struct.pack_into(">H", pkt, 4, 0x0102)  # Type
        struct.pack_into(">H", pkt, 6, 217)  # Length 217
        token_str = f"\n{token_hex}".encode("ascii")
        pkt[8 : 8 + len(token_str)] = token_str
        pkt[-7:] = b"\x3c\x00\x00" + os.urandom(3) + b"\x00"
        return bytes(pkt)

    def connect(self, token: int, timeout: float = 5.0) -> Tuple[bool, str]:
        """
        Connect to port 3900, send 68-byte APCP handshake, validate Con ID != 0,
        and send 217-byte BEEF session start packet.
        """
        try:
            self.sock = socket.create_connection(
                (self.host, self.port), timeout=timeout
            )

            # 1. 68-byte APCP Handshake
            apcp_pkt = self.build_handshake_packet()
            self.sock.sendall(apcp_pkt)

            self.sock.settimeout(timeout)
            resp1 = self.sock.recv(1024)
            if not resp1 or len(resp1) < 18:
                return False, "Short or empty response from server"

            if resp1[:4] != b"APCP":
                return False, f"Invalid server response magic: {resp1[:4]!r}"

            con_id = struct.unpack(">I", resp1[14:18])[0]
            self.session_id = con_id

            if con_id == 0:
                return (
                    False,
                    "Session rejected by IMM server (Con ID = 0 — session slot busy or token expired)",
                )

            # 2. Primary BEEF session and video initialization.
            self.sock.sendall(self.build_session_start_packet(token))
            self.sock.sendall(b"APCP\x00\x00\x00\x0c\x04\x00\x00\x00")
            self.sock.sendall(b"BEEF\x03\x0e\x00\x10\x01\x01\x00\x00\x00\x00\x00\x00")
            self.sock.sendall(b"BEEF\x03\x02\x00\x10\x04\x00\x03\x00\x00\x00\x00\x00")
            self.sock.sendall(b"BEEF\x03\x04\x00\x10\x00\x00\x00\x00\x00\x00\x00\x00")

            # 3. The official client opens a second logical APCP channel on the
            # same TCP stream. Mouse messages are ignored until this completes.
            self.running = True
            self._start_reader_thread()
            self._send_packet(self.build_secondary_handshake_packet())
            if not self.secondary_ready.wait(timeout):
                self.close()
                return False, "Timed out establishing IMM input channel"
            self._send_packet(b"BEEF\x03\x01\x00\x10\x00\x00\x00\x00\x00\x00\x00\x00")
            self._start_keepalive_thread()
            return (
                True,
                f"KVM Session Established! Con ID: {hex(con_id)} (Token: {hex(token)})",
            )
        except Exception as e:
            self.close()
            return False, f"Connection error: {e}"

    def _send_packet(self, packet: bytes):
        if not self.sock:
            raise ConnectionError("KVM socket is not connected")
        with self.send_lock:
            self.sock.sendall(packet)

    def send_keepalive(self):
        """
        Send 12-byte keepalive packet (0x0400).
        """
        if not self.sock or not self.running:
            return
        pkt = (
            b"APCP"
            + struct.pack(">I", 12)
            + struct.pack(">H", 0x0400)
            + struct.pack(">H", 0x0000)
        )
        try:
            self._send_packet(pkt)
        except Exception:
            pass

    def send_frame_ack(self):
        """
        Send 16-byte BEEF Frame ACK.
        """
        if not self.sock or not self.running:
            return
        pkt = b"BEEF\x00\x00\x00\x10\x01\x00\x00\x00\x00\x00\x00\x00"
        try:
            self._send_packet(pkt)
        except Exception:
            pass

    def send_keyboard_event(self, hid_keycode: int, is_pressed: bool = True):
        """
        Send a BEEF 0x0200 keyboard packet.

        Captured from the official client (verified with controlled keystrokes):
        body is state u16 (0x0000 down, 0x0001 up), USB HID usage u16 (e.g.
        a=0x04, Enter=0x28, Backspace=0x2A, F1=0x3A, left shift=0xE1, left
        ctrl=0xE0), then two zero u16s. Shift/Ctrl arrive as their own events
        before the key. The 0x0208/0x0204/0x0202 wake packets are prepended on
        the first send, same as mouse.
        """
        if not self.sock or not self.running or not hid_keycode:
            return
        state = 0x0000 if is_pressed else 0x0001
        pkt = b"BEEF" + struct.pack(
            ">HHHHHH", 0x0200, 16, state, hid_keycode & 0xFFFF, 0, 0
        )

        if not self.mouse_initialized:
            setup = bytes.fromhex(
                "42454546020800100100000000000000"
                "42454546020400100000000000000000"
                "42454546020200100000000000000000"
            )
            pkt = setup + pkt

        try:
            self._send_packet(pkt)
            self.mouse_initialized = True
        except Exception as exc:
            self.stream_error = f"Keyboard send failed: {exc}"

    def send_mouse_event(self, norm_x: int, norm_y: int, button_mask: int = 0):
        """
        Send a BEEF 0x0201 absolute mouse packet.

        Captured from the official client (verified with controlled left/right/
        middle clicks): body is button-mask u16, absolute X u16, absolute Y u16,
        zero u16. Mask bits: 0x0001 left, 0x0002 right, 0x0004 middle. Press =
        mask set at the position, release = mask cleared. The 0x0208/0x0204/
        0x0202 wake packets are prepended on the first send, and BEEF 0x0403 is
        sent once after the first button event (the server answers 0x8204).
        """
        if not self.sock or not self.running:
            return

        with self.video_lock:
            width = self.video_decoder.width
            height = self.video_decoder.height
        if width <= 0 or height <= 0:
            return

        norm_x = max(0, min(255, norm_x))
        norm_y = max(0, min(255, norm_y))
        x = norm_x * (width - 1) // 255
        y = norm_y * (height - 1) // 255
        move = b"BEEF" + struct.pack(
            ">HHHHHH", 0x0201, 16, button_mask & 0xFFFF, x, y, 0
        )

        if not self.mouse_initialized:
            setup = bytes.fromhex(
                "42454546020800100100000000000000"
                "42454546020400100000000000000000"
                "42454546020200100000000000000000"
            )
            move = setup + move

        try:
            self._send_packet(move)
            self.mouse_initialized = True
            if button_mask and not self.button_engaged:
                self._send_packet(
                    b"BEEF\x04\x03\x00\x10\x01\x00\x00\x00\x00\x00\x00\x00"
                )
                self.button_engaged = True
        except Exception as exc:
            self.stream_error = f"Mouse send failed: {exc}"

    def get_video_frame(
        self, after_generation: int = 0
    ) -> Optional[Tuple[int, int, int, bytes, str]]:
        """Return the newest decoded RGB frame when it is newer than the caller's."""
        with self.video_lock:
            decoder = self.video_decoder
            if decoder.frame_generation <= after_generation or not decoder.framebuffer:
                return None
            return (
                decoder.frame_generation,
                decoder.width,
                decoder.height,
                bytes(decoder.framebuffer),
                decoder.last_frame_kind,
            )

    def _queue_frame(self, kind: str, frame: bytes):
        try:
            self.frame_queue.put_nowait((kind, frame))
        except queue.Full:
            self.dropped_frames += 1

    def _start_reader_thread(self):
        self.reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self.reader_thread.start()

    def _start_keepalive_thread(self):
        self.keepalive_thread = threading.Thread(
            target=self._keepalive_loop, daemon=True
        )
        self.keepalive_thread.start()

    def _keepalive_loop(self):
        while True:
            time.sleep(10.0)
            if not self.running or not self.sock:
                return
            self.send_keepalive()

    def _reader_loop(self):
        buf = bytearray()
        while self.running and self.sock:
            try:
                data = self.sock.recv(4096)
                if not data:
                    if self.running:
                        self.stream_error = "IMM closed the KVM stream"
                    break
                buf.extend(data)

                while len(buf) >= 8:
                    if buf[:4] == b"APCP":
                        msg_len = struct.unpack(">I", buf[4:8])[0]
                        if msg_len < 8:
                            del buf[0]
                            continue
                        if len(buf) < msg_len:
                            break
                        frame = bytes(buf[:msg_len])
                        del buf[:msg_len]
                        if msg_len >= 18:
                            con_id = struct.unpack(">I", frame[14:18])[0]
                            if con_id == 4:
                                self.secondary_ready.set()
                        self._queue_frame("APCP", frame)
                        continue

                    if buf[:4] == b"BEEF" or buf[:4] == b"VID\x00":
                        msg_len = struct.unpack(">H", buf[6:8])[0]
                        if msg_len < 8:
                            del buf[0]
                            continue
                        if len(buf) < msg_len:
                            break

                        frame = bytes(buf[:msg_len])
                        del buf[:msg_len]
                        if frame[:4] == b"VID\x00":
                            self.send_frame_ack()
                            try:
                                with self.video_lock:
                                    self.video_decoder.feed(frame)
                                self.video_error = None
                            except ValueError as exc:
                                self.video_error = str(exc)
                            self._queue_frame("VID", frame)
                        else:
                            self._queue_frame("BEEF", frame)
                        continue

                    del buf[0]
            except socket.timeout:
                continue
            except Exception as exc:
                if self.running:
                    self.stream_error = f"{type(exc).__name__}: {exc}"
                break
        self.running = False

    def close(self):
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
