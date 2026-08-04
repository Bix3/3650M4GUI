import unittest
from unittest.mock import patch

import pygame

from src.imm_gui import KEY_TO_HID, IMMKVMGui, TextField, _fit_video_size


class VideoDisplayTests(unittest.TestCase):
    def test_fit_size_upscales_without_stretching(self):
        self.assertEqual(_fit_video_size(320, 200, 1023, 769), (1023, 639))

    def test_fit_size_downscales_without_stretching(self):
        self.assertEqual(_fit_video_size(1600, 1200, 1023, 769), (1023, 767))

    def test_keyboard_mapping_uses_usb_hid_usages(self):
        self.assertEqual(KEY_TO_HID[pygame.K_a], 0x04)
        self.assertEqual(KEY_TO_HID[pygame.K_RETURN], 0x28)
        self.assertEqual(KEY_TO_HID[pygame.K_LSHIFT], 0xE1)

    def test_password_field_masks_display_value(self):
        self.assertEqual(
            TextField("Password", "secret", password=True).visible_value(), "******"
        )

    def test_host_toggle_masks_display_value(self):
        gui = IMMKVMGui()
        gui.fields[0].value = "imm.lab"
        gui._layout()

        gui._handle_mouse_down(
            pygame.event.Event(
                pygame.MOUSEBUTTONDOWN, button=1, pos=gui.censor_rect.center
            )
        )
        self.assertTrue(gui.censor_host)
        self.assertEqual(gui.fields[0].visible_value(), "*******")

        gui._handle_mouse_down(
            pygame.event.Event(
                pygame.MOUSEBUTTONDOWN, button=1, pos=gui.censor_rect.center
            )
        )
        self.assertFalse(gui.censor_host)
        self.assertEqual(gui.fields[0].visible_value(), "imm.lab")

    def test_censored_host_hidden_from_connect_log(self):
        gui = IMMKVMGui()
        gui.fields[0].value = "imm.lab"
        gui.fields[1].value = "USERID"
        gui.fields[2].value = "secret"
        gui.censor_host = True

        with patch("threading.Thread") as thread:
            gui._connect()

        thread.assert_called_once()
        self.assertIn("Authenticating with *******...", gui.logs)
        self.assertFalse(any("imm.lab" in line for line in gui.logs))

    def test_power_button_dispatches_selected_action(self):
        gui = IMMKVMGui()
        gui.power_rects = [
            pygame.Rect(10, 10, 100, 40),
            pygame.Rect(10, 60, 100, 40),
            pygame.Rect(10, 110, 100, 40),
        ]

        with patch.object(gui, "_request_power") as request_power:
            gui._handle_mouse_down(
                pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(20, 120))
            )

        request_power.assert_called_once_with(2)

    def test_resize_event_does_not_reset_display_mode(self):
        gui = IMMKVMGui()
        pygame.event.post(
            pygame.event.Event(pygame.VIDEORESIZE, w=500, h=400, size=(500, 400))
        )
        pygame.event.post(pygame.event.Event(pygame.QUIT))

        with patch.object(
            pygame.display,
            "set_mode",
            side_effect=AssertionError("resize handler reset the display mode"),
        ):
            gui.run()


if __name__ == "__main__":
    unittest.main()
