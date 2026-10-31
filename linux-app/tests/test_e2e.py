#!/usr/bin/env python3
"""End-to-end: unicorn_studio.core + mqtt  <->  real mosquitto  <->  real firmware/main.py (simulated hardware).
Run: python3 tests/test_e2e.py [cosmic galactic stellar]   (needs mosquitto + mosquitto_passwd; $MOSQUITTO to override)"""
import json, os, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
from unicorn_studio import core, mqtt          # noqa: E402
from harness import Broker, Board              # noqa: E402


class Peer:
    """A client like the app: subscribes to one board and records messages."""

    def __init__(self, broker, base, name):
        self.base = base
        self.msgs = []
        self.state = core.BoardState()
        self.lock = threading.Lock()
        self.cv = threading.Condition(self.lock)
        self.c = mqtt.Client(name)
        self.up = core.Uploader(self.pub, base)
        self.plw = core.PlaylistWaiter()
        self.itw = core.ItemsWaiter()
        self.c.on_message = self.on
        self.connected = threading.Event()
        self.c.on_connect = lambda ok, why: ok and self.connected.set()
        self.c.start("127.0.0.1", broker.port, broker.user, broker.password,
                     subs=[base + "/state", base + "/status", base + "/custom/ack", base + "/custom/result",
                           base + "/custom/result2", base + "/playlist/result",
                           base + "/playlist/result2", base + "/playlist/items"])
        assert self.connected.wait(5), "client did not connect"

    def pub(self, t, p):
        self.c.publish(t, p)

    def on(self, t, p):
        if t.startswith(self.base + "/custom/"):
            self.up.feed(t, p)
        if t == self.base + "/playlist/result":
            self.plw.feed(p)
        elif t == self.base + "/playlist/result2":
            self.plw.feed2(p)
        elif t == self.base + "/playlist/items":
            self.itw.feed(p)
        with self.cv:
            self.msgs.append((t, p))
            if t == self.base + "/state":
                self.state.apply(p)
            self.cv.notify_all()

    def wait_state(self, pred, secs=8, refresh=True):
        end = time.time() + secs
        if refresh:
            self.c.publish(self.base + "/refresh/set", "1")
        with self.cv:
            while time.time() < end:
                if self.state.at and pred(self.state):
                    return True
                self.cv.wait(0.2)
        return False

    def close(self):
        self.c.stop()


def test_pattern(w, h, n=3):
    frames = []
    for k in range(n):
        px = [((x * 8 + k * 40) % 256) << 16 | ((y * 8) % 256) << 8 | (128 if (x + y + k) % 4 == 0 else 0)
              for y in range(32) for x in range(32)]
        frames.append((px, 150))
    return core.Display("test", 32, 32, frames).resized(w, h, "fit").frames


def run(kind, broker):
    print("== %s" % kind, flush=True)
    b = Board(broker, kind)
    p = Peer(broker, b.base, "t-" + kind)
    try:
        assert p.wait_state(lambda s: s.board == kind, 15, refresh=False), "no retained state from the simulated board"
        s = p.state
        W, H = core.BOARDS[kind]
        assert (s.w, s.h) == (W, H) and s.v2 and "clock" in s.modes, (s.w, s.h, s.version, s.modes)
        print("  status: %s %dx%d fw %s modes=%d rotations=%s" % (s.board, s.w, s.h, s.version, len(s.modes), s.rotations))
        # controls
        p.pub(b.base + "/light/set", json.dumps({"state": "OFF"}))
        assert p.wait_state(lambda s: not s.on), "off"
        p.pub(b.base + "/light/set", json.dumps({"state": "ON", "brightness": 128}))
        assert p.wait_state(lambda s: s.on and s.brightness == 50), "on/brightness"
        p.pub(b.base + "/mode/set", "plasma")
        assert p.wait_state(lambda s: s.mode == "plasma"), "mode"
        p.pub(b.base + "/rotation/set", "90")
        if W == H:
            assert p.wait_state(lambda s: s.rotation == "90"), "rotation 90"
        else:
            time.sleep(1)
            assert p.wait_state(lambda s: s.rotation == "0"), "galactic must refuse 90"
        p.pub(b.base + "/rotation/set", "180")
        assert p.wait_state(lambda s: s.rotation == "180"), "rotation 180"
        p.pub(b.base + "/rotation/set", "0")
        print("  on/off, brightness, mode, rotation (%s) ok" % ("/".join(s.rotations)))
        # upload success + play
        fr = test_pattern(W, H)
        r = p.up.send("lx_test", fr, play=True)
        assert r.ok, r
        assert p.wait_state(lambda s: "lx_test" in s.customs and s.mode == "custom:lx_test"), "after upload"
        stored = open(os.path.join(b.flash, "custom", "lx_test.bin"), "rb").read()
        assert stored == b"".join(core.rgb_bytes(px) for px, _ in fr), "stored bytes differ"
        print("  upload ok (%s), byte-exact on flash, now playing custom:lx_test" % r.msg)
        # lost chunk -> retry
        dropped = []
        real = p.up.publish

        def lossy(t, pl):
            hdr = 2 + pl[1] if t.endswith("/custom/frame") else 0
            if hdr and not dropped and len(fr) > 1 and (pl[hdr] << 8 | pl[hdr + 1]) == 1:
                dropped.append(1)
                return                      # drop the first chunk of frame 2 once
            real(t, pl)
        p.up.publish = lossy
        t0 = time.time()
        r = p.up.send("lx_retry", fr, play=False)
        p.up.publish = real
        assert r.ok and dropped, (r, dropped)
        print("  lost chunk recovered by retry in %.1fs" % (time.time() - t0))
        # board-side failure: bad name -> error, reported as failure
        r = p.up.send("../evil", fr[:1], play=False)
        assert not r.ok and "error" in r.msg, r
        print("  bad name refused by board -> app reports failure:", r.msg)
        # single-flight inside one app
        res = []
        th = [threading.Thread(target=lambda: res.append(p.up.send("lx_sf", fr, play=False))) for _ in range(2)]
        for t in th:
            t.start()
        for t in th:
            t.join()
        oks = [x.ok for x in res]
        assert sorted(oks) == [False, True] and any("already running" in x.msg for x in res), res
        print("  single-flight: second concurrent upload refused locally")
        # two different apps at once -> board says busy to the second; the first completes
        q = Peer(broker, b.base, "t2-" + kind)
        res2 = {}
        slow = (list(fr) * 22)[:64]
        n0 = len(p.msgs)
        t1 = threading.Thread(target=lambda: res2.__setitem__("a", p.up.send("lx_a", slow, play=False)))
        t1.start()
        while not any(t.endswith("/custom/ack") for t, _ in p.msgs[n0:]):    # first upload is under way
            time.sleep(0.005)
        res2["b"] = q.up.send("lx_b", fr, play=False)
        t1.join()
        q.close()
        assert res2["a"].ok and not res2["b"].ok and "busy" in res2["b"].msg, res2
        print("  cross-client: board refused second upload with '%s'; first succeeded" % res2["b"].msg)
        # board offline (wrong base) -> clear failure, no false success
        ghost = core.Uploader(p.pub, "unicorn/" + "0" * 6, first_wait=1, retry_wait=0.5)
        r = ghost.send("x", fr[:1])
        assert not r.ok and "did not confirm" in r.msg, r
        print("  offline board -> failure:", r.msg[:60])
        # playlist save / play / delete
        pl = {"name": "lx_list", "items": [{"mode": "custom:lx_test", "seconds": 2, "speed": 15}, {"mode": "clock", "seconds": 1},
                                           {"mode": "custom:lx_retry", "seconds": 5000}, {"mode": "custom:missing", "seconds": 3}]}
        assert p.state.has_get, p.state.version
        # a stale/foreign result2 with another id must not count as our success
        p.plw.arm("lx_list", "mine1")
        p.plw.feed2(json.dumps({"id": "other", "ok": True, "msg": "ok playlist lx_list 4 items"}))
        p.plw.feed(b"ok playlist lx_list 4 items")
        assert p.plw.reply is None
        p.pub(b.base + "/playlist/save", core.playlist_board_json(pl, "mine1"))
        reply = p.plw.wait(6)
        assert reply and reply.startswith("ok playlist lx_list 4 items"), reply
        saved = json.load(open(os.path.join(b.flash, "playlists", "lx_list.json")))
        secs = [i.get("seconds") for i in saved.get("items", saved if isinstance(saved, list) else [])]
        assert [i.get("speed") for i in saved["items"]] == [15, None, None, None], saved     # optional per-item speed kept
        # playlist/get read-back (us-2.1)
        p.itw.arm("lx_list")
        p.pub(b.base + "/playlist/get", "lx_list")
        items, err = p.itw.wait(5)
        assert items == [{"mode": "custom:lx_test", "seconds": 2, "speed": 15}, {"mode": "clock", "seconds": 2},
                         {"mode": "custom:lx_retry", "seconds": 3600}, {"mode": "custom:missing", "seconds": 3}], (items, err)
        p.itw.arm("nope")
        p.pub(b.base + "/playlist/get", "nope")
        items, err = p.itw.wait(5)
        assert items is None and err, (items, err)
        print("  playlist/get returns the stored items (speed kept); unknown name ->", err)
        p.pub(b.base + "/speed/set", "6")
        p.pub(b.base + "/mode/set", "playlist:lx_list")
        assert p.wait_state(lambda s: s.mode == "playlist:lx_list" and s.playing == "custom:lx_test"), "playlist play"
        assert p.wait_state(lambda s: s.speed_now == 15 and s.speed == 6), ("speed_now", p.state.speed_now, p.state.speed)
        print("  while the speed-15 item plays: state.speed_now=15, global speed stays 6")
        p.pub(b.base + "/speed/set", "10")
        print("  playlist saved (%s), seconds stored %s, playing %s" % (reply, secs, p.state.playing))
        # bad playlist -> error
        p.plw.arm("bad", "bad1")
        p.pub(b.base + "/playlist/save", json.dumps({"name": "bad", "id": "bad1", "items": []}))
        reply = p.plw.wait(4)
        assert reply and reply.startswith("error"), reply
        print("  empty playlist refused:", reply)
        # reboot: displays + playlist persist and resume
        p.pub(b.base + "/reboot/set", "PRESS")
        time.sleep(2.5)
        assert p.wait_state(lambda s: s.mode == "playlist:lx_list" and "lx_test" in s.customs and "lx_list" in s.playlists, 15), "after reboot"
        print("  simulated reboot: playlist + displays persisted, playlist resumed")
        # delete custom + playlist
        p.pub(b.base + "/custom/delete", "lx_retry")
        assert p.wait_state(lambda s: "lx_retry" not in s.customs), "delete custom"
        p.pub(b.base + "/playlist/delete", "lx_list")
        assert p.wait_state(lambda s: "lx_list" not in s.playlists and s.mode == "clock"), "delete playlist"
        print("  delete display + delete playlist ok; board fell back to clock")
        print("  PASS", kind, flush=True)
        return True
    finally:
        p.close()
        b.stop()


def main():
    kinds = sys.argv[1:] or ["cosmic", "galactic", "stellar"]
    br = Broker()
    ok = True
    try:
        for k in kinds:
            try:
                run(k, br)
            except AssertionError as e:
                ok = False
                print("  FAIL", k, e, flush=True)
    finally:
        br.stop()
    print("ALL PASS" if ok else "SOME FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
