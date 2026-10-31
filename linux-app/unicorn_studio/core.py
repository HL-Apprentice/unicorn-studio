"""Shared, GUI-free logic: config, library, display files, resampling, the v2 upload protocol and playlists.
Mirrors cli/unicorn and mac-app/Sources/Core.swift (see docs/PROTOCOL.md). Standard library only."""
import json, os, re, subprocess, threading, time, uuid

MAX_FRAMES = 64
CHUNK = 1024
BUILTIN_MODES = ["clock", "today", "agenda", "scroll_text", "starfield", "matrix", "plasma", "life"]
BOARDS = {"cosmic": (32, 32), "galactic": (53, 11), "stellar": (16, 16)}
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_SIDE = 256
MAX_FRAMES_READ = 1000
SECONDS_MIN, SECONDS_MAX = 2, 3600

# ---------------------------------------------------------------- paths & config


def _home():
    return os.path.expanduser("~")


def config_home():
    return os.environ.get("XDG_CONFIG_HOME") or os.path.join(_home(), ".config")


def settings_path():
    """Non-secret UI settings (host, port, user, board, TLS, library folder). Never a password."""
    return os.path.join(config_home(), "unicorn-studio", "settings.json")


def config_candidates():
    env = os.environ
    if env.get("UNICORN_CONFIG"):
        return [env["UNICORN_CONFIG"]]
    return [os.path.join(config_home(), "unicorn-studio", "config.env"),
            os.path.join(config_home(), "unicorn", "config.env")]       # shared with the CLI


def read_env_file(first):
    """KEY=VALUE file; SECRETS_FILE=/path pointers are followed (max 3 extra files). First value wins."""
    out, queue, seen = {}, [first], set()
    while queue:
        p = os.path.expanduser(os.path.expandvars(queue.pop(0)))
        if p in seen or len(seen) > 3:
            continue
        seen.add(p)
        try:
            with open(p, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except OSError:
            continue
        for line in lines:
            t = line.strip()
            if not t or t.startswith("#") or "=" not in t:
                continue
            k, v = t.split("=", 1)
            k = k.replace("export ", "").strip()
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            if k == "SECRETS_FILE":
                queue.append(v)
            elif k not in out:
                out[k] = v
    return out


def file_values():
    for c in config_candidates():
        if os.path.exists(os.path.expanduser(c)):
            return read_env_file(c)
    return {}


class ConnConfig:
    FIELDS = ("host", "port", "user", "base", "tls", "library")

    def __init__(self, host="", port=1883, user="", password="", base="", tls=False, library=""):
        self.host, self.port, self.user, self.password = host, int(port or 1883), user, password
        self.base, self.tls, self.library = base, bool(tls), library

    @property
    def complete(self):
        return bool(self.host)

    def __repr__(self):        # never show the password
        return "ConnConfig(host=%r, port=%r, user=%r, password=%s, base=%r, tls=%r)" % (
            self.host, self.port, self.user, "<set>" if self.password else "<empty>", self.base, self.tls)

    @staticmethod
    def load():
        """Precedence: environment > settings.json (non-secret) > config.env. Password: env > keyring > config.env."""
        f = file_values()
        try:
            with open(settings_path(), encoding="utf-8") as fh:
                s = json.load(fh)
            if not isinstance(s, dict):
                s = {}
        except (OSError, ValueError):
            s = {}
        e = os.environ
        c = ConnConfig()
        c.host = e.get("MQTT_BROKER") or s.get("host") or f.get("MQTT_BROKER", "")
        try:
            c.port = int(e.get("MQTT_PORT") or s.get("port") or f.get("MQTT_PORT") or 1883)
        except ValueError:
            c.port = 1883
        c.user = e.get("MQTT_USER") or s.get("user") or f.get("MQTT_USER", "")
        uid = e.get("UNICORN_ID") or f.get("UNICORN_ID", "")
        c.base = e.get("UNICORN_BASE") or s.get("base") or f.get("UNICORN_BASE") or (("unicorn/" + uid) if uid else "")
        tls = e.get("MQTT_TLS") or f.get("MQTT_TLS")
        c.tls = bool(s["tls"]) if "tls" in s and not e.get("MQTT_TLS") else (tls == "1")
        c.library = e.get("UNICORN_LIBRARY") or s.get("library") or f.get("UNICORN_LIBRARY", "")
        c.password = e.get("MQTT_PASSWORD") or keyring_get(c.user) or f.get("MQTT_PASSWORD", "")
        return c

    def save(self, remember_password=False):
        """Writes only non-secret fields. The password goes to the system keyring when asked and available."""
        write_settings({k: getattr(self, k) for k in self.FIELDS})
        if remember_password and self.password and self.user:
            return keyring_set(self.user, self.password)
        return False


UI_SETTINGS = ("canvas",)


def write_settings(data):
    """Merge non-secret settings into settings.json (mode 600). Unknown keys are dropped, so nothing secret can land there."""
    p = settings_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    try:
        with open(p, encoding="utf-8") as fh:
            cur = json.load(fh)
        if not isinstance(cur, dict):
            cur = {}
    except (OSError, ValueError):
        cur = {}
    cur.update(data)
    allowed = set(ConnConfig.FIELDS) | set(UI_SETTINGS)
    cur = {k: v for k, v in cur.items() if k in allowed}
    tmp = p + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(cur, fh, indent=2, sort_keys=True)
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


KEYRING_SERVICE = "Unicorn Studio MQTT"


def keyring_available():
    try:
        import keyring  # noqa: F401  (optional dependency: Secret Service / KWallet)
        return True
    except Exception:
        return False


def keyring_get(user):
    if not user or os.environ.get("UNICORN_NO_KEYRING"):
        return None
    try:
        import keyring
        return keyring.get_password(KEYRING_SERVICE, user)
    except Exception:
        return None


def keyring_set(user, pw):
    try:
        import keyring
        keyring.set_password(KEYRING_SERVICE, user, pw)
        return True
    except Exception:
        return False


def library_dir(override=""):
    """$UNICORN_LIBRARY > settings > XDG Documents/Unicorn Displays > ~/Documents/Unicorn Displays."""
    if os.environ.get("UNICORN_LIBRARY"):
        return os.path.expanduser(os.path.expandvars(os.environ["UNICORN_LIBRARY"]))
    if override:
        return os.path.expanduser(os.path.expandvars(override))
    docs = ""
    try:
        r = subprocess.run(["xdg-user-dir", "DOCUMENTS"], capture_output=True, text=True, timeout=2)
        docs = r.stdout.strip()
        if docs == _home():         # xdg-user-dir falls back to $HOME when unset
            docs = ""
    except Exception:
        pass
    return os.path.join(docs or os.path.join(_home(), "Documents"), "Unicorn Displays")


# ---------------------------------------------------------------- display files

class DisplayError(Exception):
    pass


def board_name(s):
    n = re.sub(r"[^a-z0-9_-]", "_", (s or "").lower())[:24]
    return n or "display"


def _hex_bytes(s):
    out = bytearray()
    hi = None
    for ch in s:
        o = ord(ch)
        if 48 <= o <= 57:
            v = o - 48
        elif 65 <= o <= 70:
            v = o - 55
        elif 97 <= o <= 102:
            v = o - 87
        else:
            continue
        if hi is None:
            hi = v
        else:
            out.append(hi << 4 | v)
            hi = None
    return bytes(out)


def _pixel_bytes(v):
    if isinstance(v, str):
        return _hex_bytes(v)
    if isinstance(v, list) and all(isinstance(x, str) for x in v):
        return _hex_bytes("".join(x.replace("#", "")[:6].rjust(6, "0") for x in v))
    if isinstance(v, list) and all(isinstance(x, int) for x in v):
        return bytes(b for x in v for b in ((x >> 16) & 255, (x >> 8) & 255, x & 255))
    return b""


class Display:
    """frames: list of (pixels: list[int 0xRRGGBB], duration_ms)."""

    def __init__(self, name, w, h, frames, desc=""):
        self.name, self.w, self.h, self.frames, self.desc = name, w, h, frames, desc

    def resized(self, W, H, mode="fit"):
        if (W, H) == (self.w, self.h):
            return self
        return Display(self.name, W, H, [(resample(px, self.w, self.h, W, H, mode), d) for px, d in self.frames], self.desc)

    def to_json(self):
        return {"format": "unicorn-display", "version": 1, "name": self.name, "description": self.desc,
                "width": self.w, "height": self.h,
                "frames": [{"duration_ms": d, "pixels": "".join("%06x" % (c & 0xFFFFFF) for c in px)} for px, d in self.frames]}


def load_display(path):
    try:
        if os.path.getsize(path) > MAX_FILE_BYTES:
            raise DisplayError("file is larger than %d MB" % (MAX_FILE_BYTES >> 20))
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError) as e:
        raise DisplayError("cannot read %s: %s" % (os.path.basename(path), e))
    if not isinstance(d, dict) or not isinstance(d.get("frames"), list):
        raise DisplayError("not a display JSON (no frames)")
    fr = d["frames"]
    if len(fr) > MAX_FRAMES_READ:
        raise DisplayError("too many frames (%d > %d)" % (len(fr), MAX_FRAMES_READ))
    w, h = d.get("width", 32), d.get("height", 32)
    first = fr[0] if fr and isinstance(fr[0], dict) else {}
    if isinstance(first.get("rows"), list) and "width" not in d:
        h = len(first["rows"])
        w = max((len(r) for r in first["rows"]), default=32)
    if not (isinstance(w, int) and isinstance(h, int) and 1 <= w <= MAX_SIDE and 1 <= h <= MAX_SIDE):
        raise DisplayError("size %sx%s not supported (max %d)" % (w, h, MAX_SIDE))
    pal = {}
    if isinstance(d.get("palette"), dict):
        for k, v in d["palette"].items():
            try:
                if k:
                    pal[k[0]] = int(str(v).replace("#", "")[:6], 16)
            except ValueError:
                pass
    frames = []
    for f in fr:
        if not isinstance(f, dict):
            continue
        px = [0] * (w * h)
        if isinstance(f.get("rows"), list):
            for y, row in enumerate(f["rows"][:h]):
                for x, ch in enumerate(str(row)[:w]):
                    px[y * w + x] = pal.get(ch, 0)
        else:
            v = f.get("pixels", f.get("hex"))
            b = _pixel_bytes(v) if v is not None else b""
            if len(b) >= w * h * 3:
                px = [b[i * 3] << 16 | b[i * 3 + 1] << 8 | b[i * 3 + 2] for i in range(w * h)]
            elif len(b) == 3072:      # legacy: 32x32 pixels with a wrong size header
                src = [b[i * 3] << 16 | b[i * 3 + 1] << 8 | b[i * 3 + 2] for i in range(1024)]
                px = resample(src, 32, 32, w, h, "stretch")
        dur = f.get("duration_ms", 200)
        dur = max(20, min(60000, dur if isinstance(dur, int) else 200))
        frames.append((px, dur))
    if not frames:
        frames = [([0] * (w * h), 200)]
    name = d.get("name") if isinstance(d.get("name"), str) else os.path.splitext(os.path.basename(path))[0]
    return Display(name, w, h, frames, d.get("description", "") if isinstance(d.get("description"), str) else "")


def save_display(disp, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(disp.to_json(), f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def resample(src, sw, sh, W, H, mode="fit"):
    """Area-average resize (same maths as the Mac app). mode: fit (letterbox), fill (crop), stretch."""
    out = [0] * (W * H)
    if sw <= 0 or sh <= 0 or len(src) != sw * sh:
        return out
    sx, sy = W / sw, H / sh
    if mode == "fit":
        sx = sy = min(sx, sy)
    elif mode == "fill":
        sx = sy = max(sx, sy)
    ox, oy = (W - sw * sx) / 2, (H - sh * sy) / 2
    import math
    for y in range(H):
        y0, y1 = (y - oy) / sy, (y + 1 - oy) / sy
        for x in range(W):
            x0, x1 = (x - ox) / sx, (x + 1 - ox) / sx
            if x1 <= 0 or y1 <= 0 or x0 >= sw or y0 >= sh:
                continue
            ix0, ix1 = max(0, math.floor(x0)), min(sw - 1, math.floor(x1 - 1e-9))
            iy0, iy1 = max(0, math.floor(y0)), min(sh - 1, math.floor(y1 - 1e-9))
            if ix1 < ix0 or iy1 < iy0:
                continue
            r = g = b = ws = 0.0
            for yy in range(iy0, iy1 + 1):
                wy = min(y1, yy + 1) - max(y0, yy)
                if wy <= 0:
                    continue
                for xx in range(ix0, ix1 + 1):
                    wx = min(x1, xx + 1) - max(x0, xx)
                    if wx <= 0:
                        continue
                    wt = wx * wy
                    c = src[yy * sw + xx]
                    r += ((c >> 16) & 255) * wt
                    g += ((c >> 8) & 255) * wt
                    b += (c & 255) * wt
                    ws += wt
            if ws <= 0 or ws / ((x1 - x0) * (y1 - y0)) < 0.5:
                continue
            out[y * W + x] = int(min(255, r / ws + 0.5)) << 16 | int(min(255, g / ws + 0.5)) << 8 | int(min(255, b / ws + 0.5))
    return out


def rgb_bytes(px):
    return bytes(b for c in px for b in ((c >> 16) & 255, (c >> 8) & 255, c & 255))


def list_library(lib):
    out = []
    if not os.path.isdir(lib):
        return out
    for root, dirs, files in os.walk(lib):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        rel_root = os.path.relpath(root, lib)
        if rel_root.split(os.sep)[0] == "Playlists":
            continue
        for f in files:
            if f.lower().endswith(".json") and not f.startswith("."):
                out.append(os.path.normpath(os.path.join(rel_root, f)))
    return sorted(out, key=lambda s: s.lower())


def seed_library(lib, src):
    """First run: copy bundled animations into an empty library."""
    if not src or not os.path.isdir(src) or list_library(lib):
        return 0
    import shutil
    n = 0
    os.makedirs(lib, exist_ok=True)
    for item in sorted(os.listdir(src)):
        s, d = os.path.join(src, item), os.path.join(lib, item)
        if os.path.exists(d):
            continue
        if os.path.isdir(s):
            shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)
        n += 1
    return n


# ---------------------------------------------------------------- playlists (stored locally, sent on request)

SPEED_MIN, SPEED_MAX, SPEED_NORMAL = 1, 20, 10


def clamp_speed(v):
    """Optional per-item playlist speed: 1..20 (10 = the display's authored timing), None = use the board's speed."""
    if v is None or v == "":
        return None
    try:
        return max(SPEED_MIN, min(SPEED_MAX, int(float(v))))
    except (TypeError, ValueError):
        return None


def _item(mode, seconds, speed=None):
    it = {"mode": mode, "seconds": clamp_seconds(seconds)}
    sp = clamp_speed(speed)
    if sp is not None:
        it["speed"] = sp
    return it


def clamp_seconds(s):
    try:
        s = int(s)
    except (TypeError, ValueError):
        s = 10
    return max(SECONDS_MIN, min(SECONDS_MAX, s))


class Playlists:
    def __init__(self, lib):
        self.dir = os.path.join(lib, "Playlists")

    def list(self):
        try:
            return sorted(f[:-5] for f in os.listdir(self.dir) if f.endswith(".json"))
        except OSError:
            return []

    def load(self, name):
        try:
            p = os.path.join(self.dir, board_name(name) + ".json")
            if os.path.getsize(p) > 1_000_000:
                return None
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            items = [_item(str(i["mode"]), i.get("seconds", 10), i.get("speed"))
                     for i in d.get("items", []) if isinstance(i, dict) and i.get("mode")]
            return {"name": board_name(d.get("name") or name), "items": items}
        except (OSError, ValueError, AttributeError, KeyError, TypeError):
            return None

    def save(self, pl):
        os.makedirs(self.dir, exist_ok=True)
        pl["name"] = board_name(pl["name"])
        p = os.path.join(self.dir, pl["name"] + ".json")
        doc = {"name": pl["name"], "items": [_item(i["mode"], i["seconds"], i.get("speed")) for i in pl["items"]]}
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2, sort_keys=True)
        os.replace(p + ".tmp", p)

    def delete(self, name):
        try:
            os.remove(os.path.join(self.dir, board_name(name) + ".json"))
        except OSError:
            pass


def missing_on_board(pl, customs):
    """Custom displays in a playlist that the board does not have. They are flagged, never uploaded by a playlist send."""
    have, out = set(customs), []
    for i in pl["items"]:
        m = i["mode"]
        if m.startswith("custom:") and m[7:] not in have and m[7:] not in out:
            out.append(m[7:])
    return out


def playlist_board_json(pl, uid=None):
    """What the board receives: mode, seconds and optional speed per item (plus the save id), never local file paths."""
    d = {"name": board_name(pl["name"]),
         "items": [_item(i["mode"], i["seconds"], i.get("speed")) for i in pl["items"][:32]]}
    if uid:
        d["id"] = str(uid)[:16]
    return json.dumps(d)


# ---------------------------------------------------------------- board state

class BoardState:
    def __init__(self):
        self.reset()

    def reset(self):
        self.on, self.brightness, self.mode, self.rotation, self.text, self.speed = True, 50, "clock", "0", "", 10
        self.customs, self.playlists, self.modes, self.playing = [], [], [], None
        self.w = self.h = 0
        self.board, self.version, self.uptime, self.reset_reason = "", "", 0, ""
        self.speed_now = None
        self.status = ""
        self.at = 0.0

    def apply(self, payload):
        try:
            j = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            return False
        if not isinstance(j, dict):
            return False
        self.on = j.get("state") == "ON"
        if isinstance(j.get("brightness"), int):
            self.brightness = round(j["brightness"] * 100 / 255)
        self.mode = str(j.get("mode", self.mode))
        self.rotation = str(j.get("rotation", self.rotation))
        self.text = str(j.get("text", self.text))
        if isinstance(j.get("speed"), int):
            self.speed = j["speed"]
        self.speed_now = j["speed_now"] if isinstance(j.get("speed_now"), int) else None
        self.customs = sorted(str(x) for x in j.get("customs") or [])
        self.playlists = sorted(str(x) for x in j.get("playlists") or [])
        self.modes = [str(x) for x in j.get("modes") or []]
        self.playing = j.get("playing") if isinstance(j.get("playing"), str) else None
        self.version = str(j.get("version", "?"))
        self.w, self.h = int(j.get("width") or 32), int(j.get("height") or 32)
        self.board = str(j.get("board") or ("cosmic" if self.w == 32 else ""))
        self.uptime = int(j.get("uptime") or 0)
        self.reset_reason = str(j.get("reset_reason") or "")
        self.at = time.time()
        return True

    @property
    def v2(self):
        return self.version.startswith("us-")

    @property
    def fw(self):
        """Firmware version as a tuple, e.g. (2, 1) for 'us-2.1'; (0,) if unknown."""
        m = re.match(r"us-(\d+)\.(\d+)", self.version)
        return (int(m.group(1)), int(m.group(2))) if m else (0,)

    @property
    def has_get(self):
        """us-2.1+: playlist/get read-back and save ids echoed in playlist/result2."""
        return self.fw >= (2, 1)

    @property
    def rotations(self):
        return ["0", "90", "180", "270"] if self.w == self.h else ["0", "180"]

    @property
    def builtins(self):
        m = [x for x in self.modes if not x.startswith(("custom:", "playlist:"))]
        return m or list(BUILTIN_MODES)

    @property
    def now_playing(self):
        return self.playing or self.mode

    def all_modes(self):
        m = self.builtins + ["custom:" + c for c in self.customs] + ["playlist:" + p for p in self.playlists]
        if self.mode not in m:
            m.append(self.mode)
        return m

    def info(self):
        if not self.at:
            return ""
        s = "%s %d×%d · fw %s · up %d min" % (self.board.capitalize(), self.w, self.h, self.version, self.uptime // 60)
        if self.reset_reason:
            s += " · last reset: " + self.reset_reason
        return s


# ---------------------------------------------------------------- uploads (protocol v2, v1 fallback)

class UploadResult:
    def __init__(self, ok, msg):
        self.ok, self.msg = ok, msg

    def __repr__(self):
        return "UploadResult(%r, %r)" % (self.ok, self.msg)


class Uploader:
    """Blocking; call send() off the UI thread. Feed every message on BASE/custom/{ack,result,result2}.
    Success is reported only when the board's final result says ok."""

    def __init__(self, publish, base, v2=True, first_wait=6.0, retry_wait=4.0, end_wait=20.0):
        self.publish, self.base, self.v2 = publish, base, v2
        self.first_wait, self.retry_wait, self.end_wait = first_wait, retry_wait, end_wait
        self._gate = threading.Lock()          # single-flight
        self._lock = threading.Lock()
        self._ev = threading.Event()
        self._active = False
        self._uid = self._want = ""
        self._acked = False
        self._result = None

    @property
    def busy(self):
        return self._gate.locked()

    def feed(self, topic, payload):
        s = payload.decode("utf-8", "replace") if isinstance(payload, (bytes, bytearray)) else str(payload)
        with self._lock:
            if not self._active:
                return
            if topic.endswith("/custom/ack"):
                if s == self._want:
                    self._acked = True
                    self._ev.set()
            elif self.v2 and topic.endswith("/custom/result2"):
                try:
                    j = json.loads(s)
                except ValueError:
                    return
                if isinstance(j, dict) and j.get("id") == self._uid:
                    self._result = UploadResult(bool(j.get("ok")), str(j.get("msg", "?")))
                    self._ev.set()
            elif not self.v2 and topic.endswith("/custom/result"):
                self._result = UploadResult(s.startswith("ok "), s)
                self._ev.set()

    def _wait(self, secs):
        self._ev.wait(max(0.0, secs))
        self._ev.clear()

    def send(self, name, frames, play=True, progress=None):
        """frames: list of (pixels, duration_ms) already at the board's size."""
        if not self._gate.acquire(blocking=False):
            return UploadResult(False, "another upload is already running")
        try:
            fr = list(frames[:MAX_FRAMES])
            if not fr:
                return UploadResult(False, "nothing to send")
            uid = uuid.uuid4().hex[:8]
            with self._lock:
                self._active, self._uid, self._want, self._acked, self._result = True, (uid if self.v2 else ""), "", False, None
                self._ev.clear()
            begin = {"name": name, "durations": [d for _, d in fr]}
            if self.v2:
                begin["id"] = uid
            self.publish(self.base + "/custom/begin", json.dumps(begin))
            for i, (px, _) in enumerate(fr):
                rgb = rgb_bytes(px)
                nch = (len(rgb) + CHUNK - 1) // CHUNK
                with self._lock:
                    self._want = ("%s:%d" % (uid, i + 1)) if self.v2 else str(i + 1)
                    self._acked = False
                ok = False
                for attempt in range(4 if self.v2 else 1):
                    for c in range(nch):
                        hdr = (bytes([0xA5, len(uid)]) + uid.encode()) if self.v2 else b""
                        self.publish(self.base + "/custom/frame", hdr + bytes([i >> 8, i & 255, c]) + rgb[c * CHUNK:(c + 1) * CHUNK])
                    deadline = time.monotonic() + (self.first_wait if attempt == 0 else self.retry_wait)
                    while time.monotonic() < deadline:
                        self._wait(deadline - time.monotonic())
                        with self._lock:
                            a, r = self._acked, self._result
                        if r is not None:
                            return UploadResult(False, "board: " + r.msg)      # any result mid-upload is an error
                        if a:
                            ok = True
                            break
                    if ok:
                        break
                if not ok:
                    return UploadResult(False, "board did not confirm frame %d of %d; nothing was saved (is the board online?)" % (i + 1, len(fr)))
                if progress:
                    progress(i + 1, len(fr))
            with self._lock:
                self._want = ""
            if self.v2:
                self.publish(self.base + "/custom/end", json.dumps({"name": name, "id": uid, "play": bool(play)}))
            else:
                self.publish(self.base + "/custom/end", name)
            deadline = time.monotonic() + self.end_wait
            while time.monotonic() < deadline:
                self._wait(deadline - time.monotonic())
                with self._lock:
                    r = self._result
                if r is not None:
                    return UploadResult(r.ok, "board: " + r.msg)
            return UploadResult(False, "no final answer from the board within %d s; it may not have saved %s" % (self.end_wait, name))
        except Exception as e:
            return UploadResult(False, "upload failed: %s" % e)
        finally:
            with self._lock:
                self._active, self._uid, self._want = False, "", ""
            self._gate.release()


class PlaylistWaiter:
    """Waits for the board's answer to a playlist save.

    us-2.1 firmware echoes the save's id in BASE/playlist/result2 {"id","ok","msg"}: only that answer counts.
    Older firmware has no id, so the text reply on BASE/playlist/result ('ok playlist <name> ...') is used instead."""

    def __init__(self):
        self._ev = threading.Event()
        self._name = None
        self._uid = None
        self.reply = None

    def arm(self, name, uid=None):
        self._name, self._uid, self.reply = name, uid, None
        self._ev.clear()

    def feed(self, payload):
        """Legacy text reply (BASE/playlist/result); ignored when waiting for an id."""
        s = payload.decode("utf-8", "replace") if isinstance(payload, (bytes, bytearray)) else str(payload)
        if self._name is None or self._uid:
            return
        if s.startswith("ok playlist %s " % self._name) or s.startswith("error playlist/"):
            self.reply = s
            self._ev.set()

    def feed2(self, payload):
        """BASE/playlist/result2: accepted only with our own id."""
        if self._name is None or not self._uid:
            return
        try:
            j = json.loads(payload)
        except (ValueError, TypeError):
            return
        if not isinstance(j, dict) or str(j.get("id", "")) != self._uid:
            return
        msg = str(j.get("msg", ""))[:200]
        ok = j.get("ok") is True and msg.startswith("ok playlist %s " % self._name)
        self.reply = msg if ok else (msg if msg.startswith("error") else "error " + (msg or "not saved"))
        self._ev.set()

    def wait(self, secs=6.0):
        self._ev.wait(secs)
        r, self._name = self.reply, None
        return r


class ItemsWaiter:
    """Waits for BASE/playlist/items {"name","items":[...]} or {"name","error"} after BASE/playlist/get (us-2.1)."""

    def __init__(self):
        self._ev = threading.Event()
        self._name = None
        self.items, self.error = None, None

    def arm(self, name):
        self._name, self.items, self.error = name, None, None
        self._ev.clear()

    def feed(self, payload):
        if self._name is None:
            return
        try:
            j = json.loads(payload)
        except (ValueError, TypeError):
            return
        if not isinstance(j, dict) or j.get("name") != self._name:
            return
        if isinstance(j.get("items"), list):
            self.items = [_item(str(i["mode"]), i.get("seconds", 10), i.get("speed"))
                          for i in j["items"][:32] if isinstance(i, dict) and isinstance(i.get("mode"), str) and i["mode"]]
        else:
            self.error = str(j.get("error") or "no items in the answer")[:120]
        self._ev.set()

    def wait(self, secs=5.0):
        self._ev.wait(secs)
        r = (self.items, self.error if self.items is not None or self.error else "no answer within %d s" % secs)
        self._name = None
        return r


# ---------------------------------------------------------------- pixel editor model (same tools as the Mac app)

PALETTE = [0xFFFFFF, 0x000000, 0xFF0000, 0xFF7800, 0xFFD200, 0x00FF00, 0x00C8A0, 0x00B4FF,
           0x0040FF, 0x8000FF, 0xFF00C8, 0xFF8CB4, 0x804020, 0x808080, 0x303030, 0x40FF80]
DUR_MIN, DUR_MAX = 20, 60000
TOOLS = ("pencil", "eraser", "fill", "picker", "text")

_FONT = {
    "A": ".#.#.#####.##.#", "B": "##.#.###.#.###.", "C": ".###..#..#...##", "D": "##.#.##.##.###.",
    "E": "####..##.#..###", "F": "####..##.#..#..", "G": ".###..#.##.#.##", "H": "#.##.#####.##.#",
    "I": "###.#..#..#.###", "J": "..#..#..##.#.#.", "K": "#.##.###.#.##.#", "L": "#..#..#..#..###",
    "M": "#.########.##.#", "N": "##.#.##.##.##.#", "O": ".#.#.##.##.#.#.", "P": "##.#.###.#..#..",
    "Q": ".#.#.##.###..##", "R": "##.#.###.#.##.#", "S": ".###...#...###.", "T": "###.#..#..#..#.",
    "U": "#.##.##.##.####", "V": "#.##.##.##.#.#.", "W": "#.##.########.#", "X": "#.##.#.#.#.##.#",
    "Y": "#.##.#.#..#..#.", "Z": "###..#.#.#..###", "0": "####.##.##.####", "1": ".#.##..#..#.###",
    "2": "##...#.#.#..###", "3": "##...#.#...###.", "4": "#.##.####..#..#", "5": "####..##...###.",
    "6": ".###..####.####", "7": "###..#.#..#..#.", "8": "####.#####.####", "9": "####.####..###.",
    "/": "..#..#.#.#..#..", "-": "......###......", "&": ".#.#.#.#.#.#.##", "?": "##...#.#.....#.",
    "+": "....#.###.#....", "(": ".#.#..#..#...#.", ")": ".#...#..#..#.#.", "#": "#.####.#.####.#",
    "<": "..#.#.#...#...#", ">": "#...#...#.#.#..", "=": "...###...###...", "*": "#.#.#.#.#.#....",
}
_NARROW = {":": ".#.#.", ".": "....#", "'": "##...", "!": "###.#", ",": "...##"}


def text_layout(text):
    """3x5 pixel font (same glyphs as the Mac app). Returns [(dx, dy)]."""
    pts, x = [], 0
    for ch in str(text).upper()[:64]:
        if ch == " ":
            x += 3
            continue
        if ch in _NARROW:
            pts += [(x, r) for r, c in enumerate(_NARROW[ch]) if c == "#"]
            x += 2
            continue
        g = (_FONT.get(ch) or _FONT["?"]).ljust(15, ".")[:15]
        pts += [(x + c, r) for r in range(5) for c in range(3) if g[r * 3 + c] == "#"]
        x += 4
    return pts


def live_payloads(px):
    """custom/live chunks for one frame: [chunk index] + up to 1024 RGB bytes each (see PROTOCOL.md)."""
    rgb = rgb_bytes(px)
    return [bytes([c]) + rgb[c * CHUNK:(c + 1) * CHUNK] for c in range((len(rgb) + CHUNK - 1) // CHUNK)]


class Editor:
    """Frames of 0xRRGGBB pixels, row-major and upright. Undo keeps 60 snapshots."""

    def __init__(self, w=32, h=32):
        self.w, self.h = w, h
        self.frames = [[[0] * (w * h), 200]]
        self.cur = 0
        self.name, self.desc, self.path = "untitled", "", None
        self.color = 0xFF7800
        self.recent = []
        self.undo_stack = []
        self.dirty = False

    # ---- state
    @property
    def px(self):
        return self.frames[self.cur][0]

    def snapshot(self):
        self.undo_stack.append([[list(p), d] for p, d in self.frames])
        if len(self.undo_stack) > 60:
            self.undo_stack.pop(0)
        self.dirty = True

    def undo(self):
        if not self.undo_stack:
            return False
        f = self.undo_stack.pop()
        if f and len(f[0][0]) == self.w * self.h:
            self.frames = f
            self.cur = min(self.cur, len(f) - 1)
            return True
        return False

    def use_color(self, c):
        self.color = c & 0xFFFFFF
        if self.color in self.recent:
            self.recent.remove(self.color)
        self.recent.insert(0, self.color)
        del self.recent[8:]

    # ---- drawing (call snapshot() once per stroke first)
    def set(self, x, y, c):
        if 0 <= x < self.w and 0 <= y < self.h:
            self.px[y * self.w + x] = c

    def line(self, a, b, c):
        (x0, y0), (x1, y1) = a, b
        dx, sx = abs(x1 - x0), (1 if x0 < x1 else -1)
        dy, sy = -abs(y1 - y0), (1 if y0 < y1 else -1)
        err = dx + dy
        while True:
            self.set(x0, y0, c)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def fill(self, x, y, c):
        if not (0 <= x < self.w and 0 <= y < self.h):
            return
        p, W, H = self.px, self.w, self.h
        t = p[y * W + x]
        if t == c:
            return
        st = [(x, y)]
        while st:
            cx, cy = st.pop()
            if cx < 0 or cy < 0 or cx >= W or cy >= H or p[cy * W + cx] != t:
                continue
            p[cy * W + cx] = c
            st += [(cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)]

    def pick(self, x, y):
        if 0 <= x < self.w and 0 <= y < self.h:
            self.use_color(self.px[y * self.w + x])

    def stamp(self, x, y, text):
        for dx, dy in text_layout(text):
            self.set(x + dx, y + dy, self.color)

    def shift(self, dx, dy):
        self.snapshot()
        W, H, src = self.w, self.h, self.px
        out = [0] * (W * H)
        for y in range(H):
            for x in range(W):
                out[((y + dy) % H) * W + (x + dx) % W] = src[y * W + x]
        self.frames[self.cur][0] = out

    def flip_h(self):
        self.snapshot()
        W, src = self.w, self.px
        self.frames[self.cur][0] = [src[y * W + W - 1 - x] for y in range(self.h) for x in range(W)]

    def clear_frame(self):
        self.snapshot()
        self.frames[self.cur][0] = [0] * (self.w * self.h)

    # ---- frames
    def add_frame(self):
        self.snapshot()
        self.frames.insert(self.cur + 1, [[0] * (self.w * self.h), self.frames[self.cur][1]])
        self.cur += 1

    def dup_frame(self):
        self.snapshot()
        p, d = self.frames[self.cur]
        self.frames.insert(self.cur + 1, [list(p), d])
        self.cur += 1

    def del_frame(self):
        if len(self.frames) <= 1:
            return False
        self.snapshot()
        del self.frames[self.cur]
        self.cur = min(self.cur, len(self.frames) - 1)
        return True

    def move_frame(self, d):
        n = self.cur + d
        if not (0 <= n < len(self.frames)):
            return False
        self.snapshot()
        self.frames[self.cur], self.frames[n] = self.frames[n], self.frames[self.cur]
        self.cur = n
        return True

    def set_duration(self, ms, all_frames=False):
        ms = max(DUR_MIN, min(DUR_MAX, int(ms)))
        if all_frames:
            self.snapshot()
            for f in self.frames:
                f[1] = ms
        elif self.frames[self.cur][1] != ms:
            self.snapshot()
            self.frames[self.cur][1] = ms

    def next_index(self):
        return (self.cur + 1) % len(self.frames)

    # ---- canvas / files
    def set_canvas(self, w, h, mode="fit"):
        if (w, h) == (self.w, self.h):
            return
        self.snapshot()
        d = self.to_display().resized(w, h, mode)
        self.w, self.h = w, h
        self.frames = [[list(p), dur] for p, dur in d.frames]
        self.undo_stack = []                         # old snapshots have the old size

    def new(self, w=None, h=None):
        color, recent = self.color, self.recent
        self.__init__(w or self.w, h or self.h)
        self.color, self.recent = color, recent

    def to_display(self):
        return Display(self.name, self.w, self.h, [(list(p), d) for p, d in self.frames], self.desc)

    def load(self, disp, path=None, mode="fit"):
        d = disp.resized(self.w, self.h, mode)
        self.frames = [[list(p), max(DUR_MIN, min(DUR_MAX, int(dur)))] for p, dur in d.frames] or [[[0] * (self.w * self.h), 200]]
        self.cur, self.name, self.desc, self.path = 0, disp.name, disp.desc, path
        self.undo_stack, self.dirty = [], False

    def save(self, path=None):
        path = path or self.path
        if not path:
            raise ValueError("no file name")
        save_display(self.to_display(), path)
        self.path, self.dirty = path, False
        return path
