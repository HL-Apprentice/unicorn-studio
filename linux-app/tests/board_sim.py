#!/usr/bin/env python3
"""Run the real firmware/main.py (unchanged, full main loop) on the desktop against a real MQTT broker.

Uses tools/fw_sim.py's hardware mocks (display, buttons, machine, micropython) and adds:
  - network.WLAN that reports "connected" (no Wi-Fi),
  - a CPython umqtt.simple with a real TCP socket,
  - a generated secrets module (test broker only),
  - a temp directory as the board's flash (custom displays, playlists, settings survive a simulated reboot).

Usage: board_sim.py --board cosmic|galactic|stellar --host 127.0.0.1 --port 1883 [--user U --password-env VAR] [--mac 020000abc123]
Test tool only: it is not shipped in the package.
"""
import argparse, os, socket, struct, sys, tempfile, time, types

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(REPO, "tools"))


class _Sock:
    """MicroPython-style socket wrapper (readinto/write/read) over a CPython socket."""

    def __init__(self, s):
        self.s = s

    def connect(self, addr):
        self.s.connect(addr)

    def fileno(self):
        return self.s.fileno()

    def settimeout(self, t):
        self.s.settimeout(t)

    def setblocking(self, b):
        self.s.setblocking(b)

    def readinto(self, mv):
        return self.s.recv_into(mv)

    def read(self, n):
        b = bytearray()
        while len(b) < n:
            c = self.s.recv(n - len(b))
            if not c:
                raise OSError("closed")
            b += c
        return bytes(b)

    def write(self, b, n=None):
        b = bytes(b) if n is None else bytes(b)[:n]
        self.s.sendall(b)
        return len(b)

    def close(self):
        self.s.close()


def make_umqtt():
    mod = types.ModuleType("umqtt.simple")

    class MQTTClient:
        def __init__(self, client_id, server, port=1883, user=None, password=None, keepalive=0):
            self.cid, self.server, self.port = client_id, server, int(port)
            self.user, self.pswd, self.keepalive = user, password, keepalive
            self.lw = None
            self.cb = None
            self.pid = 0
            self.sock = None

        def _str(self, s):
            b = s.encode() if isinstance(s, str) else bytes(s)
            return struct.pack("!H", len(b)) + b

        def _len(self, n):
            out = bytearray()
            while True:
                d = n & 0x7F
                n >>= 7
                out.append(d | (0x80 if n else 0))
                if not n:
                    return bytes(out)

        def set_callback(self, f):
            self.cb = f

        def set_last_will(self, topic, msg, retain=False, qos=0):
            self.lw = (topic, msg, retain)

        def connect(self, clean_session=True, timeout=None):
            # like micropython-lib umqtt.simple: socket.socket() + getaddrinfo via the module's own `socket`
            # (the firmware wraps it to add timeouts), connect(timeout=) on newer versions
            so = mod.socket
            self.sock = so.socket()
            if timeout is not None:
                self.sock.settimeout(timeout)
            self.sock.connect(so.getaddrinfo(self.server, self.port)[0][-1])
            flags = 0x02
            pl = self._str(self.cid)
            if self.lw:
                flags |= 0x04 | (0x20 if self.lw[2] else 0)
                pl += self._str(self.lw[0]) + self._str(self.lw[1])
            if self.user:
                flags |= 0x80
                pl += self._str(self.user)
                if self.pswd:
                    flags |= 0x40
                    pl += self._str(self.pswd)
            body = self._str("MQTT") + bytes([4, flags]) + struct.pack("!H", self.keepalive) + pl
            self.sock.write(b"\x10" + self._len(len(body)) + body)
            r = self.sock.read(4)
            if r[0] != 0x20 or r[3] != 0:
                raise OSError("CONNACK %r" % r)
            return False

        def publish(self, topic, msg, retain=False, qos=0):
            if isinstance(msg, str):
                msg = msg.encode()
            body = self._str(topic) + bytes(msg)
            self.sock.write(bytes([0x30 | (1 if retain else 0)]) + self._len(len(body)) + body)

        def subscribe(self, topic, qos=0):
            self.pid = self.pid % 65535 + 1
            body = struct.pack("!H", self.pid) + self._str(topic) + b"\x00"
            self.sock.write(b"\x82" + self._len(len(body)) + body)
            # SUBACK is read (and ignored) by the firmware's own reader (op 0x90)

        def ping(self):
            self.sock.write(b"\xc0\x00")

        def disconnect(self):
            try:
                self.sock.write(b"\xe0\x00")
            finally:
                self.sock.close()

    mod.MQTTClient = MQTTClient
    mod.socket = types.SimpleNamespace(
        socket=lambda *a: _Sock(socket.socket(socket.AF_INET, socket.SOCK_STREAM)),
        getaddrinfo=lambda h, p, *a: socket.getaddrinfo(h, p, socket.AF_INET, socket.SOCK_STREAM))
    pkg = types.ModuleType("umqtt")
    pkg.simple = mod
    sys.modules["umqtt"] = pkg
    sys.modules["umqtt.simple"] = mod


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--board", default="cosmic", choices=["cosmic", "galactic", "stellar"])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--user", default="")
    ap.add_argument("--password-env", default="", help="name of an env var holding the broker password")
    ap.add_argument("--mac", default="020000abc123", help="12 hex digits; locally administered by default")
    ap.add_argument("--flash", default="", help="directory used as the board's flash (default: temp)")
    a = ap.parse_args()

    import fw_sim
    fw_sim.setup(a.board)
    import time as tm
    tm.sleep_ms = lambda ms: time.sleep(ms / 1000.0)      # real sleeps: no busy loop
    mac = bytes.fromhex(a.mac)
    net = sys.modules["network"]

    class WLAN:
        def __init__(self, i): pass
        def active(self, *x): return True
        def config(self, *x, **k): return mac if x == ("mac",) else None
        def isconnected(self): return True
        def status(self): return 3
        def ifconfig(self): return ("192.0.2.10", "255.255.255.0", "192.0.2.1", "192.0.2.1")
        def connect(self, *x): pass
        def disconnect(self): pass
    net.WLAN = WLAN
    net.country = lambda c=None: None
    make_umqtt()
    sec = types.ModuleType("secrets")
    sec.WIFI_SSID = "sim"
    sec.WIFI_PASSWORD = "sim"
    sec.MQTT_BROKER = a.host
    sec.MQTT_PORT = a.port
    sec.MQTT_USER = a.user or "sim"           # firmware only connects when a user is set
    setattr(sec, "MQTT_PASSWORD", os.environ.get(a.password_env, "") if a.password_env else "")   # from the env, never a literal
    sec.TIME_HTTP_HOST = ""                   # no HTTP time source in the simulation
    sys.modules["secrets"] = sec

    flash = a.flash or tempfile.mkdtemp(prefix="unicorn-sim-")
    os.makedirs(flash, exist_ok=True)
    os.chdir(flash)
    src = open(os.path.join(REPO, "firmware", "main.py")).read()
    code = compile(src, "main.py", "exec")
    print("SIM %s mac=%s flash=%s" % (a.board, a.mac, flash), flush=True)
    while True:
        try:
            exec(code, {"__name__": "__main__"})
        except SystemExit as e:
            if str(e) == "reset":
                print("SIM reboot", flush=True)
                time.sleep(0.5)
                continue
            raise
        except KeyboardInterrupt:
            return


if __name__ == "__main__":
    main()
