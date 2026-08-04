# IBM x3650 M4 (IMM2) Custom KVM Client — Knowledge Base & Python Implementation

Core session setup and live video decoding are verified against the IBM System x3650 M4 IMM2 (IP omitted; use your own lab IMM).

## Verified Capabilities

1. **HTTPS Authentication**: `POST /data/login` → Session Cookie `_appwebSessionId_`.
2. **Token Minting**: `GET /designs/imm/viewer(14 params).jnlp` → Auto-binds local source IP and extracts the 32-bit KVM token.
3. **APCP Handshake**: 68-byte APCP v2.40 request → non-zero connection ID.
4. **VID Decoding**: `0x8601` differential RGB555 frames and `0x8605` video-mode notifications decode into a 24-bit RGB framebuffer.
5. **Live Rendering**: pygame scales and displays only the newest decoded framebuffer without queueing stale full-frame copies.
6. **Input Safety**: Captured BEEF mouse-move packets wake a blanked host display; mouse clicks use the verified `0x0201` button-mask encoding (left/right/middle = `0x0001`/`0x0002`/`0x0004`) and BEEF `0x0200` keyboard events with USB HID usage IDs.

## Quick Start

To launch the client GUI:

```bash
uv sync
uv run python main.py
```

## Documentation Index

- **`doc/apcp-protocol.md`**: Verified APCP wire protocol layout & response format.
- **`doc/web-api.md`**: IMM2 REST API endpoints & login parameters.
- **`doc/reverse-engineering.md`**: Bytecode disassembly, ZKM XOR string decryption key `[0x1f, 0x10, 0x28, 0x14, 0x45]`, and JNI library exports.
- **`doc/client-blueprint.md`**: Client architecture & implementation blueprint.
- **`doc/reference/viewer-sample.jnlp`**: Real JNLP sample extracted directly from IMM2.
