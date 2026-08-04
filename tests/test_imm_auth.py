import unittest

from src.imm_auth import IMMAuthenticator


class FakeResponse:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self) -> bytes:
        return self.body


class FakeOpener:
    def __init__(self, body: bytes):
        self.body = body
        self.requests = []

    def open(self, request):
        self.requests.append(request)
        return FakeResponse(self.body)


class PowerControlTests(unittest.TestCase):
    def test_power_methods_send_official_action_values(self):
        for method_name, expected_body in (
            ("power_on", b"SRVR_PwrAction:1"),
            ("power_off", b"SRVR_PwrAction:0"),
            ("power_cycle", b"SRVR_PwrAction:3"),
        ):
            with self.subTest(method=method_name):
                authenticator = IMMAuthenticator("imm.example")
                opener = FakeOpener(b'{"return":"Success"}')
                authenticator.opener = opener

                self.assertTrue(getattr(authenticator, method_name)())

                request = opener.requests[0]
                self.assertEqual(request.full_url, "https://imm.example/data?set")
                self.assertEqual(request.data, expected_body)
                self.assertEqual(
                    request.get_header("Content-type"),
                    "application/x-www-form-urlencoded",
                )

    def test_power_failure_reports_imm_reason(self):
        authenticator = IMMAuthenticator("imm.example")
        authenticator.opener = FakeOpener(
            b'{"return":"Failure","reason":"Insufficient privilege"}'
        )

        with self.assertRaisesRegex(RuntimeError, "Insufficient privilege"):
            authenticator.power_on()


if __name__ == "__main__":
    unittest.main()
