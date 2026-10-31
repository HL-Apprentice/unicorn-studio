#!/usr/bin/env python3
"""Desktop simulation of firmware/main.py for all three boards (CPython + mocks).
Exercises every mode, rotation, the upload protocol (v1 + v2 + retry + busy), playlists and
hostile input. Usage: python3 tools/fw_sim.py [cosmic|galactic|stellar ...]"""
import builtins, sys, types, time as _t, os, json, tempfile, shutil, inspect, functools, random

FW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "firmware", "main.py")
SIZES = {"cosmic": (32, 32), "galactic": (53, 11), "stellar": (16, 16)}

def setup(board):
    builtins.ptr8, builtins.ptr32 = "ptr8", "ptr32"
    mp = types.ModuleType("micropython")
    def viper(fn):
        sig = inspect.signature(fn)
        @functools.wraps(fn)
        def w(*a):
            a = list(a)
            for i, p in enumerate(sig.parameters.values()):
                if p.annotation == "ptr32":
                    a[i] = memoryview(a[i]).cast("B").cast("I")
                elif p.annotation == "ptr8":
                    a[i] = memoryview(a[i]).cast("B")
            return fn(*a)
        return w
    mp.viper = viper
    mp.native = lambda f: f
    sys.modules["micropython"] = mp
    import gc; gc.mem_free = lambda: 200000
    t0 = _t.monotonic()
    tm = sys.modules["time"]
    tm.ticks_ms = lambda: int((_t.monotonic() - t0) * 1000) + SIM["skew"]
    tm.ticks_diff = lambda a, b: a - b
    tm.ticks_add = lambda a, b: a + b
    tm.sleep_ms = lambda ms: None
    if not hasattr(tm, "_rt"):
        tm._rt = tm.time
        tm.time = lambda: int(tm._rt())    # MicroPython time.time() is an int
    m = types.ModuleType("machine")
    m.freq = lambda f: None
    m.reset = lambda: (_ for _ in ()).throw(SystemExit("reset"))
    class Timer:
        PERIODIC = 1
        def __init__(self, **k): pass
        def deinit(self): pass
    m.Timer = Timer
    class RTC:
        def datetime(self, v): pass
    m.RTC = RTC
    sys.modules["machine"] = m
    n = types.ModuleType("network")
    n.STA_IF = 0
    n.hostname = lambda h: None
    class WLAN:
        def __init__(self, i): pass
        def active(self, a): pass
        def config(self, *a, **k): return b"\x02\x00\x00\xab\xc1\x23" if a == ("mac",) else None
        def isconnected(self): return False
        def status(self): return 0
    n.WLAN = WLAN
    sys.modules["network"] = n
    w, h = SIZES[board]
    class Unicorn:
        WIDTH, HEIGHT = w, h
        SWITCH_A, SWITCH_B, SWITCH_C, SWITCH_D, SWITCH_SLEEP = 0, 1, 2, 3, 4
        SWITCH_BRIGHTNESS_UP, SWITCH_BRIGHTNESS_DOWN = 5, 6
        def is_pressed(self, b): return False
        def update(self, g): SIM["updates"] += 1
        def set_brightness(self, b): pass
    for name, cls in (("cosmic", "CosmicUnicorn"), ("galactic", "GalacticUnicorn"), ("stellar", "StellarUnicorn")):
        sys.modules.pop(name, None)
        if name == board:
            mod = types.ModuleType(name); setattr(mod, cls, Unicorn); sys.modules[name] = mod
    pg = types.ModuleType("picographics")
    pg.DISPLAY_COSMIC_UNICORN, pg.DISPLAY_GALACTIC_UNICORN, pg.DISPLAY_STELLAR_UNICORN = 1, 2, 3
    class PG(bytearray):
        def __init__(self, d):
            super().__init__(w * h * 4); self.pen = 0
        def get_bounds(self): return (w, h)
        def create_pen(self, r, g, b):
            assert 0 <= r <= 255 and 0 <= g <= 255 and 0 <= b <= 255, (r, g, b)
            return (r << 16) | (g << 8) | b
        def set_pen(self, p): self.pen = p
        def set_font(self, f): self.font = f
        def clear(self):
            mv = memoryview(self).cast("I")
            for i in range(w * h): mv[i] = self.pen
        def pixel(self, x, y):
            if 0 <= x < w and 0 <= y < h:
                memoryview(self).cast("I")[y * w + x] = self.pen
        def rectangle(self, x, y, rw, rh):
            for yy in range(y, y + rh):
                for xx in range(x, x + rw): self.pixel(xx, yy)
        def measure_text(self, t, s=1): return len(t) * 6 * s
        def text(self, t, x, y, wrap=-1, s=1):
            for i in range(len(t) * 5 * s): self.pixel(x + i, y)
    pg.PicoGraphics = PG
    sys.modules["picographics"] = pg
    sys.modules.pop("secrets", None)

SIM = {"skew": 0, "updates": 0, "pubs": []}

class FakeMQ:
    class sock:
        @staticmethod
        def settimeout(t): pass
    def publish(self, t, m, r=False):
        SIM["pubs"].append((t, m if isinstance(m, str) else bytes(m)))

def lit(ns):
    mv = memoryview(ns["g"]).cast("I")
    return sum(1 for i in range(ns["NPX"]) if mv[i])

def run_board(board):
    d = tempfile.mkdtemp(); cwd = os.getcwd(); os.chdir(d)
    try:
        setup(board)
        src = open(FW).read().rsplit("try:\n    run()", 1)[0]
        ns = {"__name__": "fw"}
        exec(compile(src, "main.py", "exec"), ns)
        ns["mq"] = FakeMQ()
        W, H, NPX = ns["W"], ns["H"], ns["NPX"]
        print("== %s %dx%d  NCH=%d  modes=%s rots=%s" % (board, W, H, ns["NCH"], ns["MODES"], ns["ROTS"]))
        ns["ntp_ok"] = True
        now = int(_t.time())
        ns["agenda_msg"](json.dumps({"tz": -25200, "ev": [
            {"t": "Weekly planning with a very long title", "s": now - 600, "e": now + 1200, "c": 1},
            {"t": "Lunch", "s": now + 3600, "e": now + 5400, "c": 0}] + [{"t": "x", "s": 1, "e": 2}] * 50}))
        assert len(ns["AG"][0]["ev"]) <= ns["MAXEV"]
        for m in ns["MODES"] + ["date"]:
            ns["set_mode"](m)
            for rot in ns["ROTS"]:
                ns["st"]["rotation"] = int(rot)
                for _ in range(3):
                    ns["tick"] += 1; ns["draw_mode"](); ns["show"]()
            print("  mode %-11s ok, lit pixels %d" % (m, lit(ns)))
        ns["st"]["rotation"] = 0
        ns["AG"][0] = None; ns["set_mode"]("agenda"); ns["draw_mode"]()
        on_msg = ns["on_msg"]; B = ns["BASE"]
        FR, NCH, CH = ns["FRAME"], ns["NCH"], ns["CHUNK"]
        def frames(n, seed):
            r = random.Random(seed)
            return [bytes(r.randrange(256) for _ in range(FR)) for _ in range(n)]
        def acks(): return [m for t, m in SIM["pubs"] if t.endswith("/custom/ack")]
        def results(): return [json.loads(m) for t, m in SIM["pubs"] if t.endswith("/result2")]
        def upload(name, fr, uid=None, drop=None, play=True):
            SIM["pubs"].clear()
            b = {"name": name, "durations": [100] * len(fr)}
            if uid: b["id"] = uid
            on_msg((B + "/custom/begin").encode(), json.dumps(b).encode())
            for i, f in enumerate(fr):
                for attempt in range(2):
                    for c in range(NCH):
                        if drop == (i, c) and attempt == 0:
                            continue
                        hdr = (bytes([0xA5, len(uid)]) + uid.encode()) if uid else b""
                        on_msg((B + "/custom/frame").encode(), hdr + bytes([i >> 8, i & 255, c]) + f[c * CH:(c + 1) * CH])
                    want = ("%s:%d" % (uid, i + 1)) if uid else str(i + 1)
                    if want in acks(): break
                else:
                    raise AssertionError("no ack for frame %d" % (i + 1))
            e = json.dumps({"name": name, "id": uid, "play": play}) if uid else name
            on_msg((B + "/custom/end").encode(), e.encode())
            return results()[-1]
        r = upload("v1test", frames(3, 1)); assert r["ok"], r
        r = upload("alien_head", frames(10, 2), uid="ab12", drop=(4, 1)); assert r["ok"], r   # lost chunk -> retry
        assert open("custom/alien_head.bin", "rb").read() == b"".join(frames(10, 2)), "stored bytes differ"
        assert ns["mode_label"]() == "custom:alien_head"
        for _ in range(3): ns["draw_mode"](); ns["show"]()
        print("  upload v1 + v2 with lost-chunk retry ok, lit %d" % lit(ns))
        # busy: second client begins while first is mid-upload
        SIM["pubs"].clear()
        on_msg((B + "/custom/begin").encode(), json.dumps({"name": "aaa", "durations": [100, 100], "id": "A"}).encode())
        on_msg((B + "/custom/begin").encode(), json.dumps({"name": "bbb", "durations": [100], "id": "B"}).encode())
        rr = results(); assert rr and rr[-1]["id"] == "B" and not rr[-1]["ok"], rr
        on_msg((B + "/custom/frame").encode(), bytes([0xA5, 1]) + b"B" + bytes([0, 0, 0]) + b"\0" * CH)   # foreign chunk ignored
        assert ns["up"]["name"] == "aaa" and ns["up"]["seq"] == 0
        ns["up_reset"]()
        print("  concurrent upload refused (busy), foreign chunks ignored")
        # hostile input
        for t, p in [("custom/begin", b'{"name":"../x","durations":[1]}'), ("custom/begin", b'{"name":"a","durations":[]}'),
                     ("custom/begin", json.dumps({"name": "a", "durations": [1] * 65}).encode()),
                     ("custom/begin", b'{"name":"a","durations":[1],"width":99}'), ("custom/begin", b"not json"),
                     ("custom/delete", b"../main"), ("custom/end", b"x"), ("custom/frame", b"\x00"),
                     ("custom/live", b"\x09" + b"\0" * 5), ("playlist/save", b'{"name":"p","items":[{"mode":"__import__"}]}'),
                     ("playlist/save", json.dumps({"name": "p", "items": [{"mode": "clock"}] * 33}).encode()),
                     ("playlist/delete", b"../../main"), ("light/set", b'{"color":{"r":9999,"g":-5,"b":"7"}}'),
                     ("rotation/set", b"45"), ("speed/set", b"1e9"), ("mode/set", b"custom:nope")]:
            on_msg((B + "/" + t).encode(), p)
        assert os.path.exists("custom") and not os.path.exists("x.bin")
        assert all(0 <= v <= 255 for v in ns["st"]["color"]) and 1 <= ns["st"]["speed"] <= 20
        ns["agenda_msg"](b"\xff\xfe"); ns["agenda_msg"](b'{"ev":[{"s":"x"}]}')
        print("  hostile inputs rejected, state sane:", ns["st"]["color"], ns["st"]["speed"], ns["st"]["rotation"])
        # playlists
        upload("ufo_purple_warp", frames(4, 3), uid="cd34", play=False)
        SIM["pubs"].clear()
        items = [{"type": "custom", "name": "ufo_purple_warp", "seconds": 10}, {"mode": "custom:alien_head", "seconds": 10},
                 {"mode": "custom:missing_one", "seconds": 10}, {"mode": "agenda", "seconds": 10},
                 {"type": "builtin", "name": "clock", "seconds": 10}, {"mode": "date", "seconds": 10}]
        on_msg((B + "/playlist/save").encode(), json.dumps({"name": "test", "items": items}).encode())
        assert "test" in ns["playlists"]()
        on_msg((B + "/mode/set").encode(), b"playlist:test")
        seen = []
        for step in range(14):
            seen.append(ns["pl"]["cur"])
            ns["draw_mode"](); ns["show"]()
            SIM["skew"] += 10001
            ns["pl_tick"](ns["time"].ticks_ms())
        print("  playlist order:", seen[:6])
        assert seen[:5] == ["custom:ufo_purple_warp", "custom:alien_head", "agenda", "clock", "today"], seen
        assert "custom:missing_one" not in seen
        assert "playlist:test" in ns["all_modes"]() and ns["mode_label"]() == "playlist:test"
        st = json.loads(ns["state_json"]()); assert st["playing"] and st["playlists"] == ["test"]
        # reboot persistence: settings saved, fresh import resumes the playlist
        ns["save_if_due"](True)
        saved = json.load(open("settings.json")); assert saved["mode"] == "playlist" and saved["playlist"] == "test"
        ns2 = {"__name__": "fw"}; exec(compile(src, "main.py", "exec"), ns2); ns2["mq"] = FakeMQ()
        ns2["init_mode"](); assert ns2["pl"]["cur"] == "custom:ufo_purple_warp", ns2["pl"]
        print("  playlist survives restart, resumes at", ns2["pl"]["cur"])
        # deleting a custom used by the playlist: skipped, no crash
        on_msg((B + "/custom/delete").encode(), b"alien_head")
        for _ in range(8):
            SIM["skew"] += 10001; ns["pl_tick"](ns["time"].ticks_ms()); ns["draw_mode"](); ns["show"]()
        # empty playlist (all missing)
        on_msg((B + "/playlist/save").encode(), json.dumps({"name": "ghost", "items": [{"mode": "custom:gone", "seconds": 2}]}).encode())
        on_msg((B + "/mode/set").encode(), b"playlist:ghost"); ns["draw_mode"](); ns["show"]()
        assert ns["RM"] is None
        on_msg((B + "/playlist/delete").encode(), b"ghost"); assert ns["mode_label"]() == "clock"
        print("  missing customs skipped; empty playlist shows EMPTY; delete falls back to clock")
        # OTA disabled by default; oversize packet path
        SIM["pubs"].clear(); ns["ota"](b"main.py\n1\nsig\nprint(1)")
        assert "disabled" in SIM["pubs"][-1][1]
        ns["OTA_KEY"] = "k" * 32
        body = b"x = 1\n"; seq = b"5"
        sig = ns["_hex"](ns["_hmac"](ns["OTA_KEY"], b"t.py\n" + seq + b"\n" + body)).encode()
        import hmac, hashlib
        assert sig.decode() == hmac.new(b"k" * 32, b"t.py\n5\n" + body, hashlib.sha256).hexdigest()
        ns["ota"](b"t.py\n" + seq + b"\n" + sig + b"\n" + body); assert SIM["pubs"][-1][1].startswith("ok"), SIM["pubs"][-1]
        ns["ota"](b"t.py\n" + seq + b"\n" + sig + b"\n" + body); assert "replay" in SIM["pubs"][-1][1]
        ns["ota"](b"t.py\n6\n" + sig + b"\n" + body); assert "signature" in SIM["pubs"][-1][1]
        ns["ota"](b"secrets.py\n9\n" + ns["_hex"](ns["_hmac"]("k" * 32, b"secrets.py\n9\nx")).encode() + b"\nx"); assert "bad name" in SIM["pubs"][-1][1]
        print("  OTA: off by default; HMAC matches Python hmac; replay, bad sig, secrets.py all refused")
        # us-2.1: playlist save carries the app's id; playlist/get lets the app import a board-only playlist
        SIM["pubs"].clear()
        on_msg((B + "/playlist/save").encode(), json.dumps({"name": "pl2", "id": "q7", "items": [{"mode": "clock", "seconds": 5}, {"mode": "custom:alien_head", "seconds": 9}]}).encode())
        r = results()[-1]; assert r["ok"] and r["id"] == "q7" and r["msg"] == "ok playlist pl2 2 items", r
        assert ns["pub_due"] is True
        SIM["pubs"].clear(); on_msg((B + "/playlist/get").encode(), b"pl2")
        got = [json.loads(m) for t, m in SIM["pubs"] if t.endswith("/playlist/items")][-1]
        assert got == {"name": "pl2", "items": [{"mode": "clock", "seconds": 5}, {"mode": "custom:alien_head", "seconds": 9}]}, got
        SIM["pubs"].clear(); on_msg((B + "/playlist/get").encode(), b"nope")
        got = [json.loads(m) for t, m in SIM["pubs"] if t.endswith("/playlist/items")][-1]; assert got.get("error"), got
        SIM["pubs"].clear(); on_msg((B + "/playlist/get").encode(), b"../x"); assert not results()[-1]["ok"]
        on_msg((B + "/playlist/items").encode(), b'{"name":"x"}')      # our own answer echoing back: ignored
        ns["need_disc"] = False; ns["pub_due"] = False; d0 = ns["dirty_t"]; on_msg((B + "/refresh/set").encode(), b"1")
        assert ns["need_disc"] is False and ns["pub_due"] is True and ns["dirty_t"] == d0, "refresh: state only (no discovery, no settings write)"
        print("  playlist save id echoed; playlist/get returns items / error; refresh no longer triggers discovery")
        # us-2.1: per-item speed (1..20, 10 = the display's own timing); absent = the global speed
        assert upload("sp_a", frames(10, 9), uid="s1", play=False)["ok"]
        SIM["pubs"].clear()
        on_msg((B + "/playlist/save").encode(), json.dumps({"name": "spd", "items": [
            {"mode": "custom:sp_a", "seconds": 5, "speed": 10}, {"mode": "starfield", "seconds": 5, "speed": 99},
            {"mode": "clock", "seconds": 5, "speed": 0}, {"mode": "custom:sp_a", "seconds": 5}]}).encode())
        assert results()[-1]["ok"], results()[-1]
        SIM["pubs"].clear(); on_msg((B + "/playlist/get").encode(), b"spd")
        got = [json.loads(m) for t, m in SIM["pubs"] if t.endswith("/playlist/items")][-1]["items"]
        assert [i.get("speed") for i in got] == [10, 20, 1, None], got
        SIM["pubs"].clear()
        on_msg((B + "/playlist/save").encode(), b'{"name":"bad","id":"e1","items":[{"mode":"clock","speed":"fast"}]}')
        r = results()[-1]; assert r["id"] == "e1" and not r["ok"], r          # the refusal carries the app's id
        assert "bad" not in ns["playlists"]()
        ns["st"]["speed"] = 6
        on_msg((B + "/mode/set").encode(), b"playlist:spd")
        sp = []
        for k in range(4):
            sp.append(ns["spd"]())
            if k == 0:     # frame timing at item speed 10: 100 ms frames advance after 100 ms, not 166
                c = ns["cust"]; c[3] = ns["time"].ticks_ms(); i0 = c[2]
                SIM["skew"] += 110; ns["draw_custom"](); assert ns["cust"][2] == (i0 + 1) % 10, "item speed 10 = own timing"
            if k == 3:     # no item speed: global 6 -> 166 ms frames, so not yet advanced after 110 ms
                c = ns["cust"]; c[3] = ns["time"].ticks_ms(); i0 = c[2]
                SIM["skew"] += 110; ns["draw_custom"](); assert ns["cust"][2] == i0, "no item speed: global speed timing"
                assert json.loads(ns["state_json"]())["speed_now"] == 6
            for _ in range(2): ns["draw_mode"](); ns["show"]()
            SIM["skew"] += 5001; ns["pl_tick"](ns["time"].ticks_ms())
        assert sp == [10, 20, 1, 6], sp
        on_msg((B + "/mode/set").encode(), b"clock")
        assert ns["spd"]() == 6 and ns["st"]["speed"] == 6, "leaving the playlist restores the global speed"
        on_msg((B + "/playlist/delete").encode(), b"spd")
        print("  per-item speed: clamped 1..20, optional (old playlists unchanged), drives frame timing; global speed back after")
        # us-2.2: discovery lives under homeassistant/<comp>/<DEV>/<key>/config so the broker ACL can be
        # "pattern write homeassistant/+/%c/+/config"; the pre-2.2 topics are cleared once (flag file disc2.txt)
        DEV = ns["DEV"]
        keys = ["display", "mode", "rotation", "text", "speed", "reboot", "ip", "uptime", "playing"]
        assert not os.path.exists("disc2.txt")
        SIM["pubs"].clear(); ns["discovery"]()
        cleared = [t for t, m in SIM["pubs"] if m == ""]
        new = {t.split("/")[3]: json.loads(m) for t, m in SIM["pubs"] if m}
        assert len(cleared) == len(keys) and all(
            len(t.split("/")) == 4 and t.split("/")[2].startswith(DEV) for t in cleared), cleared
        assert all(len(t.split("/")) == 5 and t.split("/")[2] == DEV and t.endswith("/config") for t, m in SIM["pubs"] if m)
        assert sorted(new) == sorted(keys), sorted(new)
        assert new["display"]["unique_id"] == DEV and new["mode"]["unique_id"] == DEV + "_mode", "unique ids unchanged"
        assert os.path.exists("disc2.txt")
        SIM["pubs"].clear(); ns["discovery"]()
        assert not [t for t, m in SIM["pubs"] if m == ""] and len(SIM["pubs"]) == len(keys), "old topics cleared only once"
        print("  discovery: per-device topics (ACL %c pattern), old topics cleared once, unique ids unchanged")
        # us-2.1: MQTT connect can no longer block forever (old and new umqtt connect signatures)
        class FakeSock:
            def __init__(self): self.to = None
            def settimeout(self, t): self.to = t
            def close(self): pass
        class FakeSockMod:
            @staticmethod
            def socket(*a): return FakeSock()
            @staticmethod
            def getaddrinfo(h, p): return [(0, 0, 0, "", (h, p))]
        made = []
        def make_umqtt(new_sig):
            um = types.ModuleType("umqtt.simple"); um.socket = FakeSockMod
            class C:
                def __init__(self, *a, **k): self.sock = None
                def set_last_will(self, *a, **k): pass
                def set_callback(self, f): pass
                def _go(self, timeout):
                    self.sock = um.socket.socket()
                    if new_sig: self.sock.settimeout(timeout)
                    made.append(self.sock.to)
                    if self.sock.to is None: raise AssertionError("connect would block forever")
                    raise OSError(110)        # broker unreachable: times out
            if new_sig:
                def connect(self, clean_session=True, timeout=None): self._go(timeout)
            else:
                def connect(self, clean_session=True): self._go(None)
            C.connect = connect; um.MQTTClient = C
            pk = types.ModuleType("umqtt"); pk.simple = um
            sys.modules["umqtt"] = pk; sys.modules["umqtt.simple"] = um
        for new_sig in (False, True):
            make_umqtt(new_sig)
            try: ns["mq_connect"](); raise AssertionError("expected timeout")
            except OSError: pass
        assert made == [6, 6], made
        class WL:
            disc = 0
            def isconnected(self): return True
            def disconnect(self): WL.disc += 1
            def ifconfig(self): return ("203.0.113.9",)
        ns["wlan"] = WL(); ns["SSID"] = "x"; ns["BROKER"] = "203.0.113.1"; ns["MUSER"] = "u"; ns["TIME_HOST"] = ""; ns["NTP_HOST"] = ""
        ns["mq"] = None; ns["mq_t"] = None; ns["mq_fail"] = 0
        for i in range(4):
            SIM["skew"] += 10001; ns["net_tick"](ns["time"].ticks_ms())
        assert WL.disc == 1 and ns["mq_fail"] == 0 and ns["wifi_t"] is None, (WL.disc, ns["mq_fail"])
        ns["mq"] = FakeMQ()
        print("  MQTT connect has a 6 s timeout (old + new umqtt); 4 failed connects restart Wi-Fi instead of a watchdog reset")
        print("  PASS", board)
    finally:
        os.chdir(cwd); shutil.rmtree(d)

if __name__ == "__main__":
    for b in (sys.argv[1:] or ["cosmic", "galactic", "stellar"]):
        SIM["skew"] = 0
        run_board(b)
