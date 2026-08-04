# IMM2 Web API (HTTPS, port 443)

All requests need the session cookie unless noted. Server is Appweb
(`Set-Cookie: _appwebSessionId_=…; path=/; httponly; secure`).
TLS accepts modern clients; cert is self-signed → disable verification.

## 1. Login

```
POST https://<IMM_IP>/data/login
Content-Type: application/x-www-form-urlencoded

user=USERID&password=PASSW0RD&SessionTimeout=600
```

- `SessionTimeout` is **seconds of inactivity** (UI computes `60 * {1|5|10|15|20|525600}`).
- Response (200, `Content-type: text/json`):

```json
{"status":"ok","authResult":"0","TimeWait":"0","iteSrc":"0",
 "forwardUrl":"index-console.php","redirection":"home.php","errorMsg":""}
```

- `authResult` non-zero = failure (see JS errMsg table: 1 bad creds, 5 session count
  exceeded, 13–21 ITE/password-policy codes, 99 generic, 100 system unavailable).
- Cookie jar must persist `_appwebSessionId_` for all further calls.

## 2. Mint the KVM session token (JNLP)

```
GET https://<IMM_IP>/designs/imm/viewer(<P1>@<P2>@…@<P14>).jnlp
Cookie: _appwebSessionId_=…
```

14 `@`-separated parameters (from `serverRemoteControl.js` functions `_23`/`_20`
plus the brute-forced parameter count — 13 params → `Too few parameters`, 14 → OK):

| # | Meaning | Source / value |
|---|---|---|
| 1 | IMM IP (webIP host part) | `<IMM_IP>` |
| 2 | web port | `443` |
| 3 | client IP (webIP second part) | e.g. `192.168.0.1` (the IP IMM sees you from; [INFERENCE] token may be bound to it) |
| 4 | ms epoch timestamp | `Date.now()` |
| 5 | encrypt transmission | `0` or `1` (UI checkbox `chkEncryptData`; server reported `encrypt_transmission: 0`) |
| 6 | session mode | `1` single-user, `0` multi-user |
| 7 | platform flag | `0` Win32, `1` anything else (JS `_33()`) |
| 8 | viewer type | `jnlp` (Java) or `activex` |
| 9 | allow preemption ("knock") | `0`/`1` |
| 10 | preemption timeout (min) | `0` if #9 = 0 |
| 11 | reserved | `0` |
| 12 | reserved | `0` |
| 13 | `RP_javaSecSupport` | `1` |
| 14 | reserved/padding | `0` (server rejects with `Too few parameters` if absent) |

Verified working URL:

```
/designs/imm/viewer(<IMM_IP>@443@192.168.0.1@1785523200000@0@1@1@jnlp@0@0@0@0@0@0).jnlp
```

Response: JNLP XML (see `reference/viewer-sample.jnlp`). The fields a Python client
needs:

```xml
<argument>ip=<IMM_IP></argument>
<argument>kmport=3900</argument>     <!-- keyboard/mouse port -->
<argument>vport=3900</argument>      <!-- video port (same here) -->
<argument>user=0x00000000</argument> <!-- ONE-TIME 32-bit session token (example value) -->
<argument>passwd=</argument>         <!-- always empty -->
<argument>immversion=2</argument>
<argument>vm=1</argument>            <!-- virtual media enabled -->
<argument>apcp=1</argument>          <!-- APCP protocol -->
<argument>power=1</argument>         <!-- power control allowed -->
<argument>reconnect=2</argument>
```

Notes:
- Malformed params return HTTP 200 with JSON `{"result":"fail","reason":"…","script":"…"}`
  — the `script` field still embeds a JNLP skeleton with server defaults. Useful for debugging.
- Token appears to be single/limited use and short-lived — mint it right before connecting.

## 3. Other endpoints discovered

| Endpoint | Purpose |
|---|---|
| `GET /designs/imm/dataproviders/imm_remote_control.php` | JSON: `required_java_version`, `single_user_kvm_active`, `active_kvm_session_count`, `encrypt_transmission`, `rp_session_list[]` |
| `GET /designs/imm/dataproviders/imm_rp_images.php` | JSON: mounted/server-stored virtual-media images |
| `GET /data/CheckIP` | UI session liveness check (returns 401 when session invalid) |
| `GET /data/logout` | ends session |
| `/data?set` + body | generic command channel (below) |
| `POST /designs/imm/upload/rp_image_upload.esp` | upload ISO to IMM storage |
| `GET /designs/imm/upload/rp_image_upload_status.esp?filePath=…` | upload progress |

### `/data?set` remote-presence commands (from `serverRemoteControl.js`)

Request string is sent with `loadXMLDocument("/data?set", <callback>, "<CMD>")`;
response has a `"return"` field (`"Success"` on ok).

| Command | Purpose |
|---|---|
| `RP_PreemptSession(<sessionId>,<user>,<clientIP>,<timeout>)` | ask/knock to take over an active KVM session |
| `RP_GetPreemptionStatus(<preemptId>)` | poll knock result |
| `RP_VmAllocateLoc(<filename>,<size>,<flags>)` | reserve IMM storage slot for local ISO |
| `RP_VmAllocateUrl(<user>,<pass>,<ro>,<host>,<path>)` | mount remote (CIFS/NFS/HTTP) image |
| `RP_VmMount(<slotId>)` | mount |
| `RP_VmUpdateSize(<slotId>,<size>)` | resize slot |
| `RP_VmFileStatus(<slotId>)` | upload/transfer status (5 = done) |
| `RP_GetFileIsModified(<slotId>)` | check changed |
| `RP_RemoveFile(<slotId>,<force>)` | delete image |
| `RP_VmCancelReservation(<slotId>)` | cancel reservation |

### `/data?set` server power actions (from the live `jsCommon.js`)

Send `POST /data?set` with `Content-Type: application/x-www-form-urlencoded`.
The body uses the IMM command serializer's colon delimiter, not standard form
encoding:

```
SRVR_PwrAction:<value>
```

| Value | Action |
|---|---|
| `0` | Power off immediately |
| `1` | Power on immediately |
| `3` | Restart immediately |

Success response: `{"return":"Success"}`. These values and the colon-delimited
wire format were verified against the live IMM2; `SRVR_PwrAction=...` is rejected
with `RequestFormatException`.


## 4. Headers of interest

```
Content-Security-Policy: default-src 'self';
    connect-src 'self' ws://<IMM_IP>:3900/ wss://<IMM_IP>:3900/; …
```

The `ws://…:3900` allowance looks like an HTML5-console hook, but **no JavaScript in
this firmware ever opens a WebSocket**, and port 3900 resets HTTP-upgrade requests.
Treat it as vestigial.
