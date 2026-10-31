"""Tiny MQTT 3.1.1 client (QoS 0), standard library only. Same wire behaviour as the CLI and the Mac app.

- One background reader thread; publish() is thread-safe.
- Reconnects with backoff until stop() is called; resubscribes on every connect.
- Incoming packets larger than MAX_PACKET are skipped, not buffered.
- Never logs the password (it is only written into the CONNECT packet).
"""
import socket, ssl, struct, threading, time

MAX_PACKET = 1 << 20


def _enc_str(s):
    b = s.encode() if isinstance(s, str) else bytes(s)
    return struct.pack("!H", len(b)) + b


def _enc_len(n):
    out = bytearray()
    while True:
        d = n % 128
        n //= 128
        out.append(d | (0x80 if n else 0))
        if not n:
            return bytes(out)


class MQTTError(Exception):
    pass


CONNACK_MSG = {1: "unsupported protocol", 2: "client id rejected", 3: "broker unavailable",
               4: "bad user name or password", 5: "not authorised"}


class Client:
    def __init__(self, client_id):
        self.client_id = client_id
        self.on_message = None        # fn(topic: str, payload: bytes), called on the reader thread
        self.on_connect = None        # fn(ok: bool, why: str), called on the reader thread
        self._sock = None
        self._wlock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._subs = []
        self._mid = 0
        self.connected = False

    # ---------- public ----------
    def start(self, host, port=1883, user="", password="", tls=False, subs=(), keepalive=30, timeout=5):
        self.stop()
        self._stop.clear()
        self._subs = list(subs)
        args = (host, int(port), user, password, bool(tls), keepalive, timeout)
        self._thread = threading.Thread(target=self._run, args=args, name="mqtt", daemon=True)
        self._thread.start()

    def connect_once(self, host, port=1883, user="", password="", tls=False, keepalive=30, timeout=5):
        """Blocking connect without the reconnect loop (used by tests and simple tools)."""
        self._open(host, int(port), user, password, bool(tls), keepalive, timeout)

    def stop(self):
        self._stop.set()
        s = self._sock
        if s is not None:
            try:
                with self._wlock:
                    s.sendall(b"\xe0\x00")      # DISCONNECT
            except OSError:
                pass
            try:
                s.close()
            except OSError:
                pass
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=3)
        self._thread = None
        self._sock = None
        self.connected = False

    def publish(self, topic, payload, retain=False):
        if isinstance(payload, str):
            payload = payload.encode()
        body = _enc_str(topic) + bytes(payload)
        pkt = bytes([0x30 | (1 if retain else 0)]) + _enc_len(len(body)) + body
        s = self._sock
        if s is None:
            raise MQTTError("not connected")
        with self._wlock:
            s.sendall(pkt)

    def subscribe(self, topic):
        if topic not in self._subs:
            self._subs.append(topic)
        if self._sock is not None:
            self._send_sub([topic])

    def loop_once(self, timeout=0.2):
        """Read one packet if available (only for connect_once users without the thread)."""
        self._sock.settimeout(timeout)
        try:
            self._read_packet()
        except socket.timeout:
            pass

    # ---------- internals ----------
    def _open(self, host, port, user, password, tls, keepalive, timeout):
        raw = socket.create_connection((host, port), timeout=timeout)
        if tls:
            ctx = ssl.create_default_context()
            raw = ctx.wrap_socket(raw, server_hostname=host)
        flags = 0x02
        payload = _enc_str(self.client_id)
        if user:
            flags |= 0x80
            payload += _enc_str(user)
            if password:
                flags |= 0x40
                payload += _enc_str(password)
        var = _enc_str("MQTT") + bytes([4, flags]) + struct.pack("!H", keepalive)
        body = var + payload
        raw.sendall(bytes([0x10]) + _enc_len(len(body)) + body)
        raw.settimeout(timeout)
        op, data = self._read_raw(raw)
        if op & 0xF0 != 0x20 or len(data) < 2:
            raw.close()
            raise MQTTError("no CONNACK from broker")
        if data[1] != 0:
            raw.close()
            raise MQTTError("broker refused: " + CONNACK_MSG.get(data[1], "code %d" % data[1]))
        self._sock = raw
        self.connected = True
        if self._subs:
            self._send_sub(self._subs)

    def _send_sub(self, topics):
        self._mid = self._mid % 65535 + 1
        body = struct.pack("!H", self._mid) + b"".join(_enc_str(t) + b"\x00" for t in topics)
        with self._wlock:
            self._sock.sendall(bytes([0x82]) + _enc_len(len(body)) + body)

    @staticmethod
    def _recv_exact(s, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = s.recv(min(65536, n - len(buf)))
            if not chunk:
                raise MQTTError("connection closed")
            buf += chunk
        return bytes(buf)

    def _read_raw(self, s):
        op = self._recv_exact(s, 1)[0]
        n, mul = 0, 1
        for _ in range(4):
            b = self._recv_exact(s, 1)[0]
            n += (b & 0x7F) * mul
            mul *= 128
            if not b & 0x80:
                break
        else:
            raise MQTTError("bad remaining length")
        if n > MAX_PACKET:
            left = n
            while left:
                left -= len(self._recv_exact(s, min(65536, left)))
            return op, None
        return op, self._recv_exact(s, n) if n else b""

    def _read_packet(self):
        op, data = self._read_raw(self._sock)
        if data is None or op & 0xF0 != 0x30 or len(data) < 2:
            return
        tl = struct.unpack("!H", data[:2])[0]
        topic = data[2:2 + tl].decode("utf-8", "replace")
        p = 2 + tl + (2 if op & 0x06 else 0)
        cb = self.on_message
        if cb:
            try:
                cb(topic, data[p:])
            except Exception as e:            # a UI bug must not kill the connection
                print("unicorn-studio: message handler error:", type(e).__name__, e)

    def _run(self, host, port, user, password, tls, keepalive, timeout):
        delay = 1.0
        while not self._stop.is_set():
            try:
                self._open(host, port, user, password, tls, keepalive, timeout)
            except Exception as e:
                self.connected = False
                if self.on_connect:
                    self.on_connect(False, str(e) or type(e).__name__)
                self._stop.wait(delay)
                delay = min(delay * 2, 30)
                continue
            delay = 1.0
            if self.on_connect:
                self.on_connect(True, "")
            last_ping = time.monotonic()
            try:
                while not self._stop.is_set():
                    self._sock.settimeout(1.0)
                    try:
                        self._read_packet()
                    except socket.timeout:
                        pass
                    if time.monotonic() - last_ping > keepalive / 2:
                        last_ping = time.monotonic()
                        with self._wlock:
                            self._sock.sendall(b"\xc0\x00")    # PINGREQ
            except Exception as e:
                if self._stop.is_set():
                    break
                self.connected = False
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None
                if self.on_connect:
                    self.on_connect(False, "connection lost: " + (str(e) or type(e).__name__))
                self._stop.wait(delay)
        self.connected = False
