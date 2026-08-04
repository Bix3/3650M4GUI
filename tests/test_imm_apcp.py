import struct
import unittest

from src.imm_apcp import APCPClient, APCPVideoDecoder


def build_video_packet(width: int, height: int, flags: int, payload: bytes) -> bytes:
    body = (
        b"\xde\xad\xbe\xef"
        + struct.pack(">HHB", height, width, flags)
        + b"\x00\x00\x00"
        + payload
    )
    return b"VID\x00" + struct.pack(">HH", 0x8601, len(body) + 8) + body


def build_video_mode_packet(width: int, height: int) -> bytes:
    body = b"\x02\x02" + struct.pack(">HH", height, width) + b"\x00" * 6
    return b"VID\x00" + struct.pack(">HH", 0x8605, len(body) + 8) + body


class APCPVideoDecoderTests(unittest.TestCase):
    def test_decodes_frame_split_across_packets(self):
        decoder = APCPVideoDecoder()
        first = build_video_packet(4, 2, 0x05, bytes.fromhex("fc0023"))
        second = build_video_packet(4, 2, 0x06, bytes.fromhex("44"))

        self.assertFalse(decoder.feed(first))
        self.assertTrue(decoder.feed(second))

        self.assertEqual((decoder.width, decoder.height), (4, 2))
        self.assertEqual(decoder.last_frame_kind, "pixels")
        self.assertEqual(decoder.frame_generation, 1)
        self.assertEqual(bytes(decoder.framebuffer), bytes((248, 0, 0)) * 8)

    def test_applies_two_color_pattern_and_delta_update(self):
        decoder = APCPVideoDecoder()
        initial = build_video_packet(4, 2, 0x03, bytes.fromhex("8000ffff226a"))

        self.assertTrue(decoder.feed(initial))

        black = bytes((0, 0, 0))
        white = bytes((248, 248, 248))
        self.assertEqual(
            bytes(decoder.framebuffer),
            black + white * 3 + black + white + black + white,
        )

        delta = build_video_packet(4, 2, 0x03, bytes.fromhex("07fc00"))
        self.assertTrue(decoder.feed(delta))
        self.assertEqual(bytes(decoder.framebuffer[-3:]), bytes((248, 0, 0)))
        self.assertEqual(decoder.frame_generation, 2)

    def test_video_mode_event_clears_framebuffer(self):
        decoder = APCPVideoDecoder()
        decoder.feed(build_video_packet(2, 1, 0x03, bytes.fromhex("fc0021")))

        self.assertTrue(decoder.feed(build_video_mode_packet(4, 3)))

        self.assertEqual((decoder.width, decoder.height), (4, 3))
        self.assertEqual(decoder.last_frame_kind, "mode")
        self.assertEqual(bytes(decoder.framebuffer), bytes(4 * 3 * 3))

    def test_secondary_handshake_matches_official_channel(self):
        packet = APCPClient("example.invalid").build_secondary_handshake_packet()

        self.assertEqual(len(packet), 68)
        self.assertEqual(packet[:12], bytes.fromhex("415043500000004401000102"))
        self.assertEqual(packet[12:21], bytes.fromhex("9f0225000000000420"))
        self.assertEqual(packet[53:], bytes.fromhex("00000000b4001e0000000000000000"))

    def test_mouse_move_uses_beef_channel_and_remote_coordinates(self):
        class FakeSocket:
            def __init__(self):
                self.sent = []

            def sendall(self, packet):
                self.sent.append(packet)

        client = APCPClient("example.invalid")
        client.sock = FakeSocket()
        client.running = True
        client.video_decoder.width = 1600
        client.video_decoder.height = 1200

        client.send_mouse_event(255, 128)

        packet = client.sock.sent[0]
        self.assertEqual(len(packet), 64)
        self.assertEqual(packet[:16], bytes.fromhex("42454546020800100100000000000000"))
        self.assertEqual(
            packet[16:32], bytes.fromhex("42454546020400100000000000000000")
        )
        self.assertEqual(
            packet[32:48], bytes.fromhex("42454546020200100000000000000000")
        )
        self.assertEqual(packet[48:56], bytes.fromhex("4245454602010010"))
        self.assertEqual(struct.unpack(">HHHH", packet[56:64]), (0, 1599, 601, 0))

        client.send_mouse_event(0, 0)
        self.assertEqual(len(client.sock.sent[1]), 16)
        self.assertEqual(struct.unpack(">HHHH", client.sock.sent[1][8:]), (0, 0, 0, 0))


if __name__ == "__main__":
    unittest.main()
