"""Pygame interface for the IBM System x IMM2 KVM client."""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from dataclasses import field as dataclass_field

import pygame

from src.imm_apcp import APCPClient
from src.imm_auth import IMMAuthenticator
from src.imm_config import (
    DEFAULT_PASSWORD,
    DEFAULT_USER,
)
from src.imm_config import (
    load as load_config,
)
from src.imm_config import (
    save as save_config,
)

KEY_TO_HID = {
    **{
        getattr(pygame, f"K_{letter}"): 0x04 + index
        for index, letter in enumerate("abcdefghijklmnopqrstuvwxyz")
    },
    pygame.K_1: 0x1E,
    pygame.K_2: 0x1F,
    pygame.K_3: 0x20,
    pygame.K_4: 0x21,
    pygame.K_5: 0x22,
    pygame.K_6: 0x23,
    pygame.K_7: 0x24,
    pygame.K_8: 0x25,
    pygame.K_9: 0x26,
    pygame.K_0: 0x27,
    pygame.K_RETURN: 0x28,
    pygame.K_ESCAPE: 0x29,
    pygame.K_BACKSPACE: 0x2A,
    pygame.K_TAB: 0x2B,
    pygame.K_SPACE: 0x2C,
    pygame.K_MINUS: 0x2D,
    pygame.K_EQUALS: 0x2E,
    pygame.K_LEFTBRACKET: 0x2F,
    pygame.K_RIGHTBRACKET: 0x30,
    pygame.K_BACKSLASH: 0x31,
    pygame.K_SEMICOLON: 0x33,
    pygame.K_QUOTE: 0x34,
    pygame.K_BACKQUOTE: 0x35,
    pygame.K_COMMA: 0x36,
    pygame.K_PERIOD: 0x37,
    pygame.K_SLASH: 0x38,
    pygame.K_CAPSLOCK: 0x39,
    pygame.K_F1: 0x3A,
    pygame.K_F2: 0x3B,
    pygame.K_F3: 0x3C,
    pygame.K_F4: 0x3D,
    pygame.K_F5: 0x3E,
    pygame.K_F6: 0x3F,
    pygame.K_F7: 0x40,
    pygame.K_F8: 0x41,
    pygame.K_F9: 0x42,
    pygame.K_F10: 0x43,
    pygame.K_F11: 0x44,
    pygame.K_F12: 0x45,
    pygame.K_PRINTSCREEN: 0x46,
    pygame.K_SCROLLLOCK: 0x47,
    pygame.K_PAUSE: 0x48,
    pygame.K_INSERT: 0x49,
    pygame.K_HOME: 0x4A,
    pygame.K_PAGEUP: 0x4B,
    pygame.K_DELETE: 0x4C,
    pygame.K_END: 0x4D,
    pygame.K_PAGEDOWN: 0x4E,
    pygame.K_RIGHT: 0x4F,
    pygame.K_LEFT: 0x50,
    pygame.K_DOWN: 0x51,
    pygame.K_UP: 0x52,
    pygame.K_NUMLOCK: 0x53,
    pygame.K_KP_DIVIDE: 0x54,
    pygame.K_KP_MULTIPLY: 0x55,
    pygame.K_KP_MINUS: 0x56,
    pygame.K_KP_PLUS: 0x57,
    pygame.K_KP_ENTER: 0x58,
    pygame.K_KP1: 0x59,
    pygame.K_KP2: 0x5A,
    pygame.K_KP3: 0x5B,
    pygame.K_KP4: 0x5C,
    pygame.K_KP5: 0x5D,
    pygame.K_KP6: 0x5E,
    pygame.K_KP7: 0x5F,
    pygame.K_KP8: 0x60,
    pygame.K_KP9: 0x61,
    pygame.K_KP0: 0x62,
    pygame.K_KP_PERIOD: 0x63,
    pygame.K_LCTRL: 0xE0,
    pygame.K_LSHIFT: 0xE1,
    pygame.K_LALT: 0xE2,
    pygame.K_RCTRL: 0xE4,
    pygame.K_RSHIFT: 0xE5,
    pygame.K_RALT: 0xE6,
}

BUTTON_MASK = {1: 0x0001, 2: 0x0004, 3: 0x0002}
POWER_ACTION_LABELS = ("Power On", "Power Off", "Restart Server")

BACKGROUND = (13, 17, 23)
PANEL = (22, 27, 34)
PANEL_ALT = (33, 38, 45)
BORDER = (48, 54, 61)
TEXT = (201, 209, 217)
MUTED = (139, 148, 158)
BLUE = (88, 166, 255)
GREEN = (63, 185, 80)
YELLOW = (227, 179, 65)
RED = (248, 81, 73)


def _fit_video_size(
    source_width: int, source_height: int, available_width: int, available_height: int
) -> tuple[int, int]:
    if available_width * source_height <= available_height * source_width:
        return available_width, max(1, available_width * source_height // source_width)
    return max(1, available_height * source_width // source_height), available_height


@dataclass
class TextField:
    label: str
    value: str
    password: bool = False
    rect: pygame.Rect = dataclass_field(default_factory=lambda: pygame.Rect(0, 0, 1, 1))

    def visible_value(self) -> str:
        return "*" * len(self.value) if self.password else self.value


class IMMKVMGui:
    def __init__(self) -> None:
        pygame.init()
        pygame.display.set_caption("IBM System x IMM2 Remote Control Client")
        self.screen = pygame.display.set_mode((1200, 800), pygame.RESIZABLE)
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont("sans", 20)
        self.small_font = pygame.font.SysFont("monospace", 16)
        self.title_font = pygame.font.SysFont("sans", 22, bold=True)

        saved = load_config()
        self.fields = [
            TextField("IMM Host", saved.get("host", "")),
            TextField("User", saved.get("user", DEFAULT_USER)),
            TextField(
                "Password", saved.get("password", DEFAULT_PASSWORD), password=True
            ),
        ]
        self.active_field: int | None = None
        self.connecting = False
        self.authenticator: IMMAuthenticator | None = None
        self.apcp_client: APCPClient | None = None
        self.current_token: int | None = None
        self.power_action_in_progress: str | None = None
        self.worker_events: queue.Queue[tuple] = queue.Queue()

        self.logs = ["Ready"]
        self.status = "Ready"
        self.frame_count = 0
        self.bytes_received = 0
        self.video_frame_count = 0
        self.video_generation = 0
        self.last_stream_error: str | None = None

        self.video_frame: tuple[int, int, bytes] | None = None
        self.video_surface: pygame.Surface | None = None
        self.scaled_surface: pygame.Surface | None = None
        self.scaled_key: tuple[int, int, int] | None = None
        self.video_display_rect = pygame.Rect(0, 0, 1, 1)
        self.video_panel_rect = pygame.Rect(0, 0, 1, 1)
        self.last_mouse_norm = (128, 128)
        self.pressed_button_mask = 0

        self.connect_rect = pygame.Rect(0, 0, 1, 1)
        self.censor_host = False
        self.censor_label = self.small_font.render("Hide host", True, MUTED)
        self.censor_rect = pygame.Rect(0, 0, 1, 1)
        self.power_rects: list[pygame.Rect] = []
        pygame.key.start_text_input()

    def run(self) -> None:
        running = True
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type in (pygame.VIDEORESIZE, pygame.WINDOWSIZECHANGED):
                    # Pygame 2 resizes the display surface before delivering the
                    # event. Calling set_mode() here fights the window manager.
                    self.scaled_key = None
                else:
                    self._handle_event(event)

            self._drain_worker_events()
            self._poll_frames()
            self._draw()
            pygame.display.flip()
            self.clock.tick(60)

        if self.apcp_client:
            self.apcp_client.close()
        pygame.quit()

    def _handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.MOUSEBUTTONDOWN:
            self._handle_mouse_down(event)
        elif event.type == pygame.MOUSEBUTTONUP:
            self._handle_mouse_up(event)
        elif event.type == pygame.MOUSEMOTION:
            self._handle_mouse_motion(event)
        elif event.type == pygame.KEYDOWN:
            self._handle_key_down(event)
        elif event.type == pygame.KEYUP:
            self._send_key(event.key, False)
        elif event.type == pygame.TEXTINPUT and self.active_field is not None:
            self.fields[self.active_field].value += event.text

    def _handle_mouse_down(self, event: pygame.event.Event) -> None:
        for index, field in enumerate(self.fields):
            if field.rect.collidepoint(event.pos):
                self.active_field = index
                return

        self.active_field = None
        if self.connect_rect.collidepoint(event.pos):
            self._connect()
            return
        if self.censor_rect.collidepoint(event.pos):
            self.censor_host = not self.censor_host
            self.fields[0].password = self.censor_host
            return
        if event.button == 1:
            for index, rect in enumerate(self.power_rects):
                if rect.collidepoint(event.pos):
                    self._request_power(index)
                    return

        if event.button in BUTTON_MASK and self.video_panel_rect.collidepoint(
            event.pos
        ):
            self.pressed_button_mask = BUTTON_MASK[event.button]
            self._send_mouse(event.pos, self.pressed_button_mask)

    def _handle_mouse_up(self, event: pygame.event.Event) -> None:
        if event.button in BUTTON_MASK and self.pressed_button_mask:
            self.pressed_button_mask = 0
            self._send_mouse(event.pos, 0)

    def _handle_mouse_motion(self, event: pygame.event.Event) -> None:
        if self.video_panel_rect.collidepoint(event.pos):
            self._send_mouse(event.pos, self.pressed_button_mask)

    def _handle_key_down(self, event: pygame.event.Event) -> None:
        if self.active_field is not None:
            if event.key == pygame.K_BACKSPACE:
                self.fields[self.active_field].value = self.fields[
                    self.active_field
                ].value[:-1]
            elif event.key == pygame.K_TAB:
                self.active_field = (self.active_field + 1) % len(self.fields)
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self._connect()
            return
        self._send_key(event.key, True)

    def _send_key(self, key: int, pressed: bool) -> None:
        if self.active_field is not None:
            return
        client = self.apcp_client
        hid = KEY_TO_HID.get(key)
        if client and client.running and hid:
            client.send_keyboard_event(hid, is_pressed=pressed)

    def _send_mouse(self, position: tuple[int, int], button_mask: int) -> None:
        client = self.apcp_client
        if not client or not client.running or self.video_surface is None:
            return
        rect = self.video_display_rect
        x = max(0, min(rect.width - 1, position[0] - rect.left))
        y = max(0, min(rect.height - 1, position[1] - rect.top))
        norm_x = x * 255 // max(1, rect.width - 1)
        norm_y = y * 255 // max(1, rect.height - 1)
        self.last_mouse_norm = (norm_x, norm_y)
        client.send_mouse_event(norm_x, norm_y, button_mask=button_mask)

    def _request_power(self, action_index: int) -> None:
        authenticator = self.authenticator
        if authenticator is None:
            self.status = "Connect to the IMM before using power controls"
            self._log("Power command rejected: no authenticated IMM session")
            return
        if self.power_action_in_progress is not None:
            return

        label = POWER_ACTION_LABELS[action_index]
        self.power_action_in_progress = label
        self.status = f"Sending {label} command..."
        self._log(f"Sending {label} command to IMM...")
        threading.Thread(
            target=self._power_worker,
            args=(authenticator, action_index, label),
            daemon=True,
        ).start()

    def _power_worker(
        self, authenticator: IMMAuthenticator, action_index: int, label: str
    ) -> None:
        action = (
            authenticator.power_on,
            authenticator.power_off,
            authenticator.power_cycle,
        )[action_index]
        try:
            action()
        except (OSError, RuntimeError, ValueError) as exc:
            self.worker_events.put(("power_failed", label, str(exc)))
        else:
            self.worker_events.put(("power_succeeded", label))

    def _connect(self) -> None:
        if self.connecting:
            return
        if self.power_action_in_progress is not None:
            self.status = f"Wait for {self.power_action_in_progress} to finish"
            return
        host, user, password = (field.value.strip() for field in self.fields)
        if not host or not user or not password:
            self.status = "Host, user, and password are required"
            self._log("Connection rejected: missing credentials")
            return

        if self.apcp_client:
            self.apcp_client.close()
            self.apcp_client = None
        self.authenticator = None
        self.connecting = True
        self.active_field = None
        self.status = "Logging in to IMM..."
        shown_host = "*" * len(host) if self.censor_host else host
        self._log(f"Authenticating with {shown_host}...")
        threading.Thread(
            target=self._connect_worker,
            args=(host, user, password),
            daemon=True,
        ).start()

    def _connect_worker(self, host: str, user: str, password: str) -> None:
        try:
            authenticator = IMMAuthenticator(host, user, password)
            if not authenticator.login():
                self.worker_events.put(
                    ("failed", "IMM login failed; check credentials")
                )
                return
            self.worker_events.put(("authenticated", authenticator))
            try:
                save_config(host, user, password)
            except OSError as exc:
                self.worker_events.put(("log", f"Config save failed: {exc}"))
            self.worker_events.put(("status", "Minting KVM token..."))
            token = authenticator.mint_kvm_token()
            self.worker_events.put(("log", f"Token obtained: {hex(token)}"))
            self.worker_events.put(("status", "Connecting to APCP port 3900..."))
            client = APCPClient(host, 3900)
            success, detail = client.connect(token)
            if success:
                self.worker_events.put(
                    ("connected", authenticator, client, token, detail)
                )
            else:
                client.close()
                self.worker_events.put(("failed", detail))
        except Exception as exc:  # noqa: BLE001 - report worker failures to the UI
            self.worker_events.put(("failed", str(exc)))

    def _drain_worker_events(self) -> None:
        while True:
            try:
                event = self.worker_events.get_nowait()
            except queue.Empty:
                return
            kind = event[0]
            if kind == "log":
                self._log(event[1])
            elif kind == "status":
                self.status = event[1]
            elif kind == "authenticated":
                self.authenticator = event[1]
                self._log("IMM web session authenticated; power controls enabled.")
            elif kind == "failed":
                self.connecting = False
                self.status = f"Connection failed: {event[1]}"
                self._log(f"ERROR: {event[1]}")
            elif kind == "power_succeeded":
                self.power_action_in_progress = None
                self.status = f"{event[1]} command accepted by IMM"
                self._log(self.status)
            elif kind == "power_failed":
                self.power_action_in_progress = None
                self.status = f"{event[1]} failed: {event[2]}"
                self._log(f"ERROR: {self.status}")
            elif kind == "connected":
                _, self.authenticator, self.apcp_client, self.current_token, detail = (
                    event
                )
                self.connecting = False
                self.status = "Connected to IMM KVM"
                self._log(f"APCP: {detail}")
                self._log("Mouse buttons and keyboard enabled.")
                self.frame_count = 0
                self.bytes_received = 0
                self.video_frame_count = 0
                self.video_generation = 0
                self.last_stream_error = None
                self.video_frame = None
                self.video_surface = None
                self.scaled_surface = None
                self.scaled_key = None

    def _poll_frames(self) -> None:
        client = self.apcp_client
        if not client:
            return

        while True:
            try:
                _, frame_data = client.frame_queue.get_nowait()
            except queue.Empty:
                break
            self.frame_count += 1
            self.bytes_received += len(frame_data)

        decoded = client.get_video_frame(self.video_generation)
        if decoded:
            generation, width, height, rgb, frame_kind = decoded
            self.video_generation = generation
            self.video_frame = (width, height, rgb)
            self.video_surface = pygame.image.frombuffer(rgb, (width, height), "RGB")
            self.scaled_key = None
            if frame_kind == "pixels":
                self.video_frame_count += 1
                self.status = (
                    f"KVM video {width}x{height} | decoded frames: {self.video_frame_count} "
                    f"| packets: {self.frame_count}"
                )
            else:
                if not client.mouse_initialized:
                    client.send_mouse_event(128, 128)
                self.status = f"KVM video mode {width}x{height}; waiting for pixels"

        error = client.stream_error or client.video_error
        if error and error != self.last_stream_error:
            self.last_stream_error = error
            self._log(f"STREAM ERROR: {error}")
        if not client.running:
            self.status = f"KVM stream disconnected: {error or 'stream reader stopped'}"

    def _layout(self) -> None:
        width, height = self.screen.get_size()
        margin = 10
        toolbar_height = 102
        status_height = 36
        sidebar_width = 260

        available = width - 2 * margin
        connect_width = 160
        toggle_width = self.censor_label.get_width() + 28
        label_widths = (86, 45, 86)
        fixed = sum(label_widths) + connect_width + toggle_width + 8 * 8
        field_total = max(300, available - fixed)
        host_width = max(120, field_total * 45 // 100)
        user_width = max(90, field_total * 22 // 100)
        password_width = max(110, field_total - host_width - user_width)

        x = margin + 8
        y = margin + 43
        for field, label_width, field_width in zip(
            self.fields, label_widths, (host_width, user_width, password_width)
        ):
            x += label_width
            field.rect = pygame.Rect(x, y, field_width, 46)
            x += field_width + 8
        self.connect_rect = pygame.Rect(x, y, connect_width, 46)
        self.censor_rect = pygame.Rect(x + connect_width + 8, y, toggle_width, 46)

        content_top = margin + toolbar_height + 5
        content_bottom = height - status_height - margin
        sidebar = pygame.Rect(
            margin, content_top, sidebar_width, content_bottom - content_top
        )
        self.video_panel_rect = pygame.Rect(
            sidebar.right + 8,
            content_top,
            width - sidebar.right - 18,
            content_bottom - content_top,
        )
        self.power_rects = [
            pygame.Rect(
                sidebar.left + 12, sidebar.top + 52 + index * 58, sidebar.width - 24, 48
            )
            for index in range(3)
        ]

    def _draw(self) -> None:
        self._layout()
        self.screen.fill(BACKGROUND)
        width, height = self.screen.get_size()
        toolbar = pygame.Rect(10, 10, width - 20, 102)
        pygame.draw.rect(self.screen, PANEL, toolbar, border_radius=4)
        pygame.draw.rect(self.screen, BORDER, toolbar, 1, border_radius=4)
        self.screen.blit(self.small_font.render("IMM Connection", True, TEXT), (18, 16))

        for index, field in enumerate(self.fields):
            label = self.small_font.render(field.label, True, MUTED)
            self.screen.blit(
                label,
                (
                    field.rect.left - label.get_width() - 6,
                    field.rect.centery - label.get_height() // 2,
                ),
            )
            color = BLUE if self.active_field == index else BORDER
            pygame.draw.rect(self.screen, BACKGROUND, field.rect, border_radius=5)
            pygame.draw.rect(self.screen, color, field.rect, 2, border_radius=5)
            value = field.visible_value()
            rendered = self.font.render(value, True, TEXT)
            clip = self.screen.get_clip()
            self.screen.set_clip(field.rect.inflate(-14, -6))
            self.screen.blit(
                rendered,
                (field.rect.left + 7, field.rect.centery - rendered.get_height() // 2),
            )
            self.screen.set_clip(clip)

        self._draw_button(
            self.connect_rect,
            "Connecting..." if self.connecting else "Connect KVM",
            enabled=not self.connecting,
        )
        self._draw_host_toggle()
        self._draw_sidebar()
        self._draw_video()

        status_rect = pygame.Rect(10, height - 38, width - 20, 28)
        pygame.draw.rect(self.screen, PANEL_ALT, status_rect)
        status_surface = self.small_font.render(self.status, True, TEXT)
        self.screen.blit(
            status_surface, status_surface.get_rect(midleft=(15, status_rect.centery))
        )

    def _draw_host_toggle(self) -> None:
        box = pygame.Rect(0, 0, 18, 18)
        box.midleft = (self.censor_rect.left, self.censor_rect.centery)
        hovered = self.censor_rect.collidepoint(pygame.mouse.get_pos())
        pygame.draw.rect(self.screen, BACKGROUND, box, border_radius=3)
        pygame.draw.rect(
            self.screen,
            BLUE if (self.censor_host or hovered) else BORDER,
            box,
            2,
            border_radius=3,
        )
        if self.censor_host:
            pygame.draw.line(
                self.screen,
                BLUE,
                (box.left + 4, box.centery),
                (box.left + 7, box.bottom - 5),
                2,
            )
            pygame.draw.line(
                self.screen,
                BLUE,
                (box.left + 7, box.bottom - 5),
                (box.right - 4, box.top + 4),
                2,
            )
        self.screen.blit(
            self.censor_label,
            (
                box.right + 6,
                self.censor_rect.centery - self.censor_label.get_height() // 2,
            ),
        )

    def _draw_sidebar(self) -> None:
        if not self.power_rects:
            return
        sidebar = pygame.Rect(
            self.power_rects[0].left - 12,
            self.video_panel_rect.top,
            self.power_rects[0].width + 24,
            self.video_panel_rect.height,
        )
        pygame.draw.rect(self.screen, PANEL, sidebar, border_radius=4)
        pygame.draw.rect(self.screen, BORDER, sidebar, 1, border_radius=4)
        self.screen.blit(
            self.title_font.render("Server Power", True, TEXT),
            (sidebar.left + 12, sidebar.top + 14),
        )
        power_enabled = (
            self.authenticator is not None and self.power_action_in_progress is None
        )
        for rect, label in zip(self.power_rects, POWER_ACTION_LABELS):
            self._draw_button(rect, label, enabled=power_enabled)

        log_top = self.power_rects[-1].bottom + 24
        self.screen.blit(
            self.title_font.render("Session Log", True, TEXT),
            (sidebar.left + 12, log_top),
        )
        y = log_top + 34
        max_lines = max(1, (sidebar.bottom - y - 10) // 20)
        for line in self.logs[-max_lines:]:
            rendered = self.small_font.render(line[:32], True, MUTED)
            self.screen.blit(rendered, (sidebar.left + 12, y))
            y += 20

    def _draw_video(self) -> None:
        panel = self.video_panel_rect
        pygame.draw.rect(self.screen, PANEL, panel)
        pygame.draw.rect(self.screen, BORDER, panel, 1)
        inner = panel.inflate(-8, -8)

        if self.video_surface is None or self.video_frame is None:
            title = "IMM2 KVM DISPLAY STREAM"
            detail = "STREAM INITIALIZING" if self.apcp_client else "DISCONNECTED"
            title_surface = self.title_font.render(title, True, BLUE)
            detail_surface = self.font.render(
                detail, True, YELLOW if self.apcp_client else MUTED
            )
            self.screen.blit(
                title_surface,
                title_surface.get_rect(center=(inner.centerx, inner.centery - 16)),
            )
            self.screen.blit(
                detail_surface,
                detail_surface.get_rect(center=(inner.centerx, inner.centery + 16)),
            )
            self.video_display_rect = pygame.Rect(inner.centerx, inner.centery, 1, 1)
            return

        width, height, _ = self.video_frame
        display_width, display_height = _fit_video_size(
            width, height, inner.width, inner.height
        )
        key = (self.video_generation, display_width, display_height)
        if self.scaled_key != key:
            if (display_width, display_height) == (width, height):
                self.scaled_surface = self.video_surface
            else:
                self.scaled_surface = pygame.transform.scale(
                    self.video_surface, (display_width, display_height)
                )
            self.scaled_key = key
        self.video_display_rect = pygame.Rect(0, 0, display_width, display_height)
        self.video_display_rect.center = inner.center
        if self.scaled_surface:
            self.screen.blit(self.scaled_surface, self.video_display_rect)

    def _draw_button(self, rect: pygame.Rect, label: str, enabled: bool = True) -> None:
        hovered = rect.collidepoint(pygame.mouse.get_pos())
        fill = (40, 105, 170) if hovered and enabled else PANEL_ALT
        text_color = TEXT if enabled else MUTED
        pygame.draw.rect(self.screen, fill, rect, border_radius=5)
        pygame.draw.rect(
            self.screen, BLUE if enabled else BORDER, rect, 2, border_radius=5
        )
        rendered = self.font.render(label, True, text_color)
        self.screen.blit(rendered, rendered.get_rect(center=rect.center))

    def _log(self, message: str) -> None:
        self.logs.append(message)
        if len(self.logs) > 200:
            del self.logs[:-200]


def main() -> None:
    IMMKVMGui().run()


if __name__ == "__main__":
    main()
