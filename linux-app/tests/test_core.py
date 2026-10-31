#!/usr/bin/env python3
"""Unit tests for unicorn_studio.core (no network, no Qt). Run: python3 tests/test_core.py"""
import io, json, os, sys, tempfile, contextlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from unicorn_studio import core  # noqa: E402

FAKE_PW = "pw-" + os.urandom(6).hex()      # random per run; checked to never leak


def t_config():
    d = tempfile.mkdtemp()
    sec = os.path.join(d, "secrets.env")
    open(sec, "w").write("MQTT_PASSWORD='%s'\nMQTT_USER=ignored\n" % FAKE_PW)
    cfg = os.path.join(d, "config.env")
    open(cfg, "w").write("# comment\nexport MQTT_BROKER=broker.example.org\nMQTT_USER=studio\nUNICORN_ID=abc123\n"
                         "SECRETS_FILE=%s\nSECRETS_FILE=%s\n" % (sec, cfg))      # loop is harmless
    env = {"UNICORN_CONFIG": cfg, "XDG_CONFIG_HOME": d, "UNICORN_NO_KEYRING": "1"}
    old = {k: os.environ.get(k) for k in list(env) + ["MQTT_BROKER", "MQTT_PASSWORD", "MQTT_USER", "UNICORN_BASE", "UNICORN_LIBRARY"]}
    for k in old:
        os.environ.pop(k, None)
    os.environ.update(env)
    try:
        c = core.ConnConfig.load()
        assert c.host == "broker.example.org" and c.user == "studio" and c.password == FAKE_PW, c
        assert c.base == "unicorn/abc123"
        assert FAKE_PW not in repr(c) and "<set>" in repr(c)
        c.save(remember_password=True)          # keyring disabled -> not stored anywhere
        txt = open(core.settings_path()).read()
        assert FAKE_PW not in txt and "password" not in txt, txt
        assert oct(os.stat(core.settings_path()).st_mode & 0o777) == "0o600"
        os.environ["MQTT_PASSWORD"] = "from-env"
        assert core.ConnConfig.load().password == "from-env"
        os.environ["UNICORN_LIBRARY"] = "$XDG_CONFIG_HOME/lib"
        assert core.library_dir("") == os.path.join(d, "lib")
        del os.environ["UNICORN_LIBRARY"]
        assert core.library_dir("/x/y") == "/x/y"
        assert core.library_dir("").endswith("Unicorn Displays")
        # --check-config output never contains the password
        from unicorn_studio import app
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            app.main(["--check-config"])
        assert "from-env" not in buf.getvalue() and FAKE_PW not in buf.getvalue() and "password     : set" in buf.getvalue()
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    print("  config: SECRETS_FILE pointer, env override, password never in settings/repr/--check-config")


def t_display():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "a.json")
    json.dump({"palette": {"r": "#ff0000", ".": "000000"}, "frames": [{"rows": ["r.", ".r"], "duration_ms": 5}]}, open(p, "w"))
    x = core.load_display(p)
    assert (x.w, x.h) == (2, 2) and x.frames[0] == ([0xFF0000, 0, 0, 0xFF0000], 20), x.frames
    json.dump({"width": 2, "height": 1, "frames": [{"pixels": "00ff00#0000ff", "duration_ms": 99999}, {"hex": ["#010203", "040506"]}]}, open(p, "w"))
    x = core.load_display(p)
    assert x.frames[0] == ([0x00FF00, 0x0000FF], 60000) and x.frames[1][0] == [0x010203, 0x040506]
    json.dump({"width": 53, "height": 11, "frames": [{"pixels": "ff" * 3072}]}, open(p, "w"))      # legacy 32x32 data
    x = core.load_display(p)
    assert len(x.frames[0][0]) == 53 * 11 and x.frames[0][0][0] == 0xFFFFFF
    for bad in ({"frames": "x"}, {"width": 999, "frames": [{}]}, [1, 2], {"frames": [{}] * 1001}):
        json.dump(bad, open(p, "w"))
        try:
            core.load_display(p)
            raise AssertionError("accepted %r" % (bad,))
        except core.DisplayError:
            pass
    open(p, "w").write("{not json")
    try:
        core.load_display(p)
        raise AssertionError("accepted bad json")
    except core.DisplayError:
        pass
    # round trip
    disp = core.Display("Hello World!", 3, 1, [([1, 2, 3], 100)])
    core.save_display(disp, os.path.join(d, "out", "h.json"))
    y = core.load_display(os.path.join(d, "out", "h.json"))
    assert y.frames == disp.frames and core.board_name(y.name) == "hello_world_"
    print("  display files: rows+palette, pixels/hex, legacy, limits, hostile input, round trip")


def t_resample():
    src = [0xFFFFFF] * (32 * 32)
    for (W, H) in core.BOARDS.values():
        for m in ("fit", "fill", "stretch"):
            out = core.resample(src, 32, 32, W, H, m)
            assert len(out) == W * H
    fit = core.resample(src, 32, 32, 53, 11, "fit")
    assert fit[0] == 0 and fit[26] == 0xFFFFFF          # letterboxed sides, white centre
    assert all(v == 0xFFFFFF for v in core.resample(src, 32, 32, 53, 11, "fill"))
    half = core.resample([0xFF0000, 0x0000FF] * 1, 2, 1, 1, 1, "stretch")
    assert half == [0x800080], hex(half[0])
    assert core.resample([1, 2, 3, 4], 2, 2, 4, 4, "fit")[0] == 1          # exact upscale is crisp
    print("  resample: fit/fill/stretch for 32x32, 53x11, 16x16; area average exact")


def t_playlists():
    d = tempfile.mkdtemp()
    s = core.Playlists(d)
    s.save({"name": "My List!", "items": [{"mode": "custom:a", "seconds": 1, "file": "Imported/a.json", "speed": 99},
                                          {"mode": "clock", "seconds": 99999}, {"mode": "plasma", "seconds": 5, "speed": "x"}]})
    assert s.list() == ["my_list_"]
    p = s.load("my_list_")
    assert p["items"] == [{"mode": "custom:a", "seconds": 2, "speed": 20}, {"mode": "clock", "seconds": 3600},
                          {"mode": "plasma", "seconds": 5}], p
    assert "Imported" not in open(os.path.join(d, "Playlists", "my_list_.json")).read()   # no local paths stored or sent
    j = json.loads(core.playlist_board_json(p))
    assert j == {"name": "my_list_", "items": [{"mode": "custom:a", "seconds": 2, "speed": 20}, {"mode": "clock", "seconds": 3600},
                                               {"mode": "plasma", "seconds": 5}]}
    assert json.loads(core.playlist_board_json(p, "abc"))["id"] == "abc"
    w = core.PlaylistWaiter(); w.arm("x", "id1")
    w.feed(b"ok playlist x 1 items"); w.feed2('{"id":"id2","ok":true,"msg":"ok playlist x 1 items"}'); assert w.reply is None
    w.feed2('{"id":"id1","ok":true,"msg":"ok playlist x 1 items"}'); assert w.wait(0) == "ok playlist x 1 items"
    w.arm("x", "id3"); w.feed2('{"id":"id3","ok":false,"msg":"error playlist/save: bad"}'); assert w.wait(0).startswith("error")
    w.arm("x"); w.feed(b"ok playlist x 2 items"); assert w.wait(0) == "ok playlist x 2 items"      # pre-2.1 firmware
    iw = core.ItemsWaiter(); iw.arm("x")
    iw.feed('{"name":"y","items":[]}'); iw.feed('{"name":"x","items":[{"mode":"clock","seconds":1,"speed":30},{"bad":1},"z"]}')
    assert iw.wait(0) == ([{"mode": "clock", "seconds": 2, "speed": 20}], None)
    iw.arm("x"); iw.feed('{"name":"x","error":"no such playlist"}'); assert iw.wait(0) == (None, "no such playlist")
    iw.arm("x"); assert iw.wait(0)[1].startswith("no answer")
    bs = core.BoardState(); bs.apply(json.dumps({"version": "us-2.1", "speed": 6, "speed_now": 15}))
    assert bs.has_get and bs.speed_now == 15 and not core.BoardState().has_get
    assert core.clamp_speed(0) == 1 and core.clamp_speed(10) == 10 and core.clamp_speed(None) is None
    assert core.missing_on_board(p, ["b"]) == ["a"] and core.missing_on_board(p, ["a"]) == []
    os.makedirs(os.path.join(d, "Sub"))
    open(os.path.join(d, "Sub", "x.json"), "w").write("{}")
    assert core.list_library(d) == [os.path.join("Sub", "x.json")]          # Playlists/ excluded
    s.delete("my_list_")
    assert s.list() == []
    print("  playlists: names sanitised, seconds 2..3600, optional speed 1..20, no file paths, missing displays flagged")


def t_editor():
    e = core.Editor(8, 4)
    e.snapshot(); e.line((0, 0), (7, 3), 0xFF0000)
    assert e.px[0] == 0xFF0000 and e.px[3 * 8 + 7] == 0xFF0000 and sum(1 for v in e.px if v) == 8
    e.snapshot(); e.fill(7, 0, 0x00FF00)
    assert e.px[7] == 0x00FF00 and e.px[3 * 8] == 0                     # fill stops at the line
    assert e.undo() and e.px[7] == 0
    e.pick(0, 0); assert e.color == 0xFF0000 and e.recent == [0xFF0000]
    e.dup_frame(); e.add_frame(); assert len(e.frames) == 3 and e.cur == 2 and not any(e.px)
    e.set_duration(5); assert e.frames[2][1] == core.DUR_MIN
    e.set_duration(300, True); assert all(d == 300 for _, d in e.frames)
    assert e.move_frame(-1) and e.cur == 1 and not any(e.frames[1][0])
    assert e.del_frame() and len(e.frames) == 2
    e.cur = 0; e.shift(1, 0); assert e.px[1] == 0xFF0000
    e.flip_h(); assert e.px[6] == 0xFF0000
    e.snapshot(); e.stamp(0, 0, "I"); assert e.px[0] and e.px[1] and e.px[2]
    pts = core.text_layout("HI:"); assert pts and max(x for x, _ in pts) < 12
    p = os.path.join(tempfile.mkdtemp(), "t.json")
    e.name = "Test Pic"; e.save(p)
    d = core.load_display(p)
    assert (d.w, d.h) == (8, 4) and [list(x) for x, _ in d.frames] == [f[0] for f in e.frames] and d.name == "Test Pic"
    e2 = core.Editor(16, 8); e2.load(d, p)
    assert (e2.w, e2.h) == (16, 8) and len(e2.frames) == 2 and not e2.dirty
    e2.set_canvas(8, 4, "stretch"); assert len(e2.px) == 32
    ch = core.live_payloads([0x010203] * (32 * 32))
    assert len(ch) == 3 and [c[0] for c in ch] == [0, 1, 2] and len(ch[0]) == 1025 and len(ch[2]) == 1 + 3072 - 2048
    print("  editor model: line, fill, undo, eyedropper, frames, durations, shift/flip, text, save/load, resize, live chunks")


def t_state():
    s = core.BoardState()
    assert s.apply(json.dumps({"state": "ON", "brightness": 255, "mode": "playlist:x", "customs": ["b", "a"], "playlists": ["x"],
                               "modes": ["clock", "today"], "playing": "custom:a", "board": "galactic", "width": 53, "height": 11,
                               "version": "us-2.0", "rotation": "180"}))
    assert s.rotations == ["0", "180"] and s.v2 and s.now_playing == "custom:a" and s.customs == ["a", "b"]
    assert s.all_modes() == ["clock", "today", "custom:a", "custom:b", "playlist:x"]
    assert not s.apply(b"\xff") and not s.apply("[1]")
    print("  board state parsing")


if __name__ == "__main__":
    for f in (t_config, t_display, t_resample, t_playlists, t_editor, t_state):
        f()
    print("ALL PASS")
