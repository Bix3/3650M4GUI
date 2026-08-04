"""
IMM2 Authentication & Token Minting Module

Handles HTTPS login to IBM System x IMM2 management module, cookie persistence,
and fetching dynamic 32-bit KVM session tokens.
"""

import json
import http.cookiejar
import re
import socket
import ssl
import time
import urllib.parse
import urllib.request


def get_local_ip_to(target_host: str) -> str:
    """
    Determine local IP address used on the interface reaching target_host.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((target_host, 3900))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


class IMMAuthenticator:
    def __init__(self, host: str, user: str = "USERID", password: str = "PASSW0RD"):
        self.host = host
        self.user = user
        self.password = password
        self.cookie_jar = http.cookiejar.CookieJar()
        self.opener = self._build_opener()

    def _build_opener(self) -> urllib.request.OpenerDirector:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        handler = urllib.request.HTTPSHandler(context=ctx)
        cookie_handler = urllib.request.HTTPCookieProcessor(self.cookie_jar)
        return urllib.request.build_opener(handler, cookie_handler)

    def login(self, timeout_sec: int = 600) -> bool:
        """
        Authenticate against IMM2 POST /data/login.
        """
        login_url = f"https://{self.host}/data/login"
        post_params = {
            "user": self.user,
            "password": self.password,
            "SessionTimeout": str(timeout_sec),
        }
        encoded_data = urllib.parse.urlencode(post_params).encode("utf-8")
        req = urllib.request.Request(
            login_url,
            data=encoded_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        with self.opener.open(req) as resp:
            data = resp.read().decode("utf-8", errors="ignore")
            return '"status":"ok"' in data

    def mint_kvm_token(self, client_ip: str = None) -> int:
        """
        Request dynamic JNLP file from IMM2 to extract the 32-bit KVM session token.
        Token is bound to client_ip.
        """
        if not client_ip:
            client_ip = get_local_ip_to(self.host)

        ts = int(time.time() * 1000)
        jnlp_url = (
            f"https://{self.host}/designs/imm/viewer("
            f"{self.host}@443@{client_ip}@{ts}@0@1@1@jnlp@0@0@0@0@0@0"
            f").jnlp"
        )
        req = urllib.request.Request(jnlp_url)
        with self.opener.open(req) as resp:
            content = resp.read().decode("utf-8", errors="ignore")

        match = re.search(r"<argument>user=(0x[0-9A-Fa-f]+)</argument>", content)
        if match:
            return int(match.group(1), 16)
        raise RuntimeError("Failed to extract KVM session token from JNLP response.")

    def _power_action(self, action: int) -> bool:
        request = urllib.request.Request(
            f"https://{self.host}/data?set",
            data=f"SRVR_PwrAction:{action}".encode("ascii"),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with self.opener.open(request) as response:
            result = json.loads(response.read().decode("utf-8", errors="ignore"))

        if result.get("return") == "Success":
            return True
        reason = result.get("reason") or result.get("return") or "unknown error"
        raise RuntimeError(f"IMM power action failed: {reason}")

    def power_on(self) -> bool:
        """Power on immediately (official SRVR_PwrAction value 1)."""
        return self._power_action(1)

    def power_off(self) -> bool:
        """Power off immediately (official SRVR_PwrAction value 0)."""
        return self._power_action(0)

    def power_cycle(self) -> bool:
        """Restart immediately (official SRVR_PwrAction value 3)."""
        return self._power_action(3)
