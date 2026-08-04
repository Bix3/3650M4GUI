# Reverse-engineering notes — Avocent/IBM KVM Java client

Artifacts pulled straight from the IMM (any session cookie works; the files are
static and versioned):

| File | Size | Content |
|---|---|---|
| `/designs/imm/aessrp/avctIBMViewer__V030321.jar` | 1,169,898 B | main client, 803 classes, obfuscated (ZKM 5.0.3 — Zelix KlassMaster) |
| `/designs/imm/aessrp/avctKVMIOLinux64__V030321.jar` | 78,472 B | `libavctKVMIO.so` — local keyboard-grab JNI |
| `/designs/imm/aessrp/avctVMAPI_DLLLinux64__V030321.jar` | 3,180,638 B | `libVMAPI_DLL.so` — virtual media JNI |
| (Win32/Win64/Linux32/Mac64 variants of the two native jars) | | same libs per-OS |

JNLP entry point: `com.avocent.ibmc.kvm.Main`, `j2se 1.5+/1.6+`, `all-permissions`.

## 1. ZKM string decryption (needed to read ANY string in the JAR)

Every class carries an encrypted string table (`static String[] z`) plus a
`<clinit>` XOR loop. For `com/avocent/d/a/a.class` the key extracted from
bytecode is:

```python
KEY = [0x1f, 0x10, 0x28, 0x14, 0x45]

def zkm_decrypt(enc: str) -> str:
    return "".join(chr(ord(c) ^ KEY[i % len(KEY)]) for i, c in enumerate(enc))
```

Proof: `zkm_decrypt("^@kD") == "APCP"` (the protocol magic, `writeBytes(z[24])`).
Note: the XOR key is per-class; extract each class's key from its own `<clinit>`
tail (`bipush` constants feeding the `ixor` in the decrypt loop).

Decrypted string table of `com/avocent/d/a/a` (index → plaintext, abridged):

```
 0 ResourceFile
 6 ProtocolAPCP
 9  SSL_DHE_DSS_WITH_DES_CBC_SHA
17 SSL_RSA_WITH_RC4_128_SHA
18 Unsupported connection type requested (capabilities=
19 SSL_RSA_WITH_3DES_EDE_CBC_SHA
24 APCP                                ← request magic
26 SSL_RSA_WITH_RC4_128_MD5
27 Header incorrect
29 Header read timed out
33 connection closed due to 0 con id
34  Connecting to:                     ← redirect-port log line
36 RECONNECT_SUPPORT
37 ProtocolAPCP: Request version:
39 TLSv1.2   40 TLSv1.1   41 SSL   42 TLSv1.0
45 sending session req
47 APCP Version =
```

## 2. Class map (what matters for a rewrite)

```
com/avocent/ibmc/kvm/Main                     entry: parses JNLP args, builds controller
com/avocent/ibmc/kvm/IBMViewerMainController  menus, SSLSocketFactory default cipher scan
com/avocent/ibmc/kvm/IBMConfigManager         config

com/avocent/d/a/a   ProtocolAPCP — session setup request/response, TLS upgrade,
                    reconnect. THE class for the handshake. (Analyzed fully.)
com/avocent/d/a/c   X509TrustManager (accept-all)
com/avocent/d/a/d   keepalive/monitor thread (writeBytes/writeInt/writeShort loop)
com/avocent/d/c/b   session controller — owns socket/streams/timers, starts APCP,
                    reconnect bookkeeping, dispatches frames
com/avocent/d/c/n,q,r,s,o,m,p   stream/event helper classes
com/avocent/d/g/a   frame reader loop (readInt/readByte/readFully → frame parse)
com/avocent/d/d/b/c frame dispatcher + session recorder (ThreadPoolExecutor,
                    FileOutputStream/RandomAccessFile recording files)
com/avocent/b/c/a/a DataInputStream wrapper (InputStream, Socket)
com/avocent/b/c/a/b DataOutputStream wrapper
com/avocent/e/e     environment: t() / u() → connection-type / capability ints
com/avocent/e/k     feature-flag map (RECONNECT_SUPPORT etc.)
com/avocent/kvm/a/b/*   keyboard/mouse event encoders
com/avocent/kvm/nativekeyboard/NativeKVM  JNI decl for libavctKVMIO.so
a/a/a/*             heavily obfuscated codec (video tile decompress) — a/a/a/d/e
                    is the largest class (11.9 KB)
com/avocent/vm/*    virtual media UI; talks JNI → libVMAPI_DLL.so
```

## 3. Native libraries (JNI exports)

`libavctKVMIO.so` (69 KB) — only local-input hooks, NOT needed for a Python client
(we deliver key/mouse events ourselves):

```
Java_com_avocent_kvm_nativekeyboard_NativeKVM_{determineDriver,getKVMIOVersion,
  getLEDKeyStatus,getLibraryId,getWindowId,registerWindowById,setCursorLocation,
  setPassthroughEnabled,setProcessWindowsMessages,unregisterWindowById}
```

`libVMAPI_DLL.so` (3.1 MB) — virtual media (`RPVMNativeLibrary_*`): Connect,
MapLocalDrive, CreateImage, UploadServerImage, GetDriveList, SendPreemptRequest…
Needed only if the Python client will mount ISOs; can also be skipped entirely by
driving the `/data?set` + `rp_image_upload.esp` HTTP path instead.

`com/avocent/vm/jni/VMSessionStatus` disconnect-reason enum worth mirroring:

```
DISCONNECT_REQUESTED, CONNECTION_FAILED_NETWORK_PROBLEM, RECONNECTING,
RECONNECT_COMPLETED, RECONNECT_FAILED, VM_SESSION_CLOSED,
REASON_ADMIN_DISCONNECT, REASON_SESSION_TIMEOUT, REASON_KVM_LOCK_DISCONNECT
```

## 4. Tooling used (reproduce the analysis)

No JVM needed. The analysis above was done with a ~120-line Python class-file
parser (constant pool + `Code` attribute walker + opcode table) and the XOR
decryptor above. Same approach works for the remaining unknowns (frame format,
input packet IDs): parse `com/avocent/d/g/a`, `com/avocent/kvm/a/b/*`,
`a/a/a/d/e` and read the `Data{Input,Output}Stream` call sequences.
