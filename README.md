# IBM x3650 M4 (IMM2) Custom KVM Client

Python remote-control client for the IBM System x3650 M4 Integrated Management Module 2 (IMM2) KVM, with live video decoding verified against a real IMM2.

## Why

The stock IMM2 web interface only offers KVM through a Java applet (needing an ancient, vulnerable Java runtime) or ActiveX (needing old IE on Windows). Both are long dead on modern systems. This project is a standalone Python and pygame client with no JVM, browser plugins, or legacy runtimes. It speaks the IMM2's APCP/BEEF protocol directly and gives you live video, keyboard, and mouse from any modern machine.

## Quick Start

```bash
uv sync
uv run python main.py
```

## Layout

- `main.py` — GUI entry point
- `src/imm_gui.py` — pygame viewer GUI
- `src/imm_apcp.py` — APCP session & VID stream decoding
- `src/imm_auth.py` — IMM2 HTTPS authentication, KVM token minting, and server power control
- `tools/mitm_proxy.py` — local proxy for capturing official-client traffic
- `tests/` — unit tests
- `doc/` — protocol documentation and reverse-engineering notes

## Documentation

See [doc/README.md](doc/README.md) for verified capabilities and the full documentation index.
