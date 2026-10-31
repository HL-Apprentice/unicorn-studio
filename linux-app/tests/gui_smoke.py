#!/usr/bin/env python3
"""Headless GUI smoke test: the real Qt window, driven by code, against mosquitto + the simulated firmware.
Run under Xvfb:  xvfb-run -a python3 tests/gui_smoke.py [cosmic galactic stellar]
(or QT_QPA_PLATFORM=offscreen). Uses a throwaway HOME; touches nothing of yours."""
import json, os, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
from harness import Broker, Board  # noqa: E402

SHOTS = os.environ.get("UNICORN_SHOTS", "")


def run(kind, br):
    home = tempfile.mkdtemp(prefix="unicorn-home-")
    os.environ.update({"HOME": home, "XDG_CONFIG_HOME": os.path.join(home, ".config"), "UNICORN_NO_KEYRING": "1",
                       "UNICORN_LIBRARY": os.path.join(home, "lib")})
    for k in ("MQTT_BROKER", "MQTT_PASSWORD", "MQTT_USER", "UNICORN_BASE", "UNICORN_CONFIG"):
        os.environ.pop(k, None)
    os.makedirs(os.path.join(home, ".config", "unicorn-studio"))
    cfgp = os.path.join(home, ".config", "unicorn-studio", "config.env")
    with open(cfgp, "w") as f:
        f.write("MQTT_BROKER=127.0.0.1\nMQTT_PORT=%d\nMQTT_USER=%s\nMQTT_PASSWORD=%s\n" % (br.port, br.user, br.password))
    os.chmod(cfgp, 0o600)
    lib = os.environ["UNICORN_LIBRARY"]
    os.makedirs(os.path.join(lib, "Starters"))
    for n, col in (("Red Heart", 0xFF2040), ("blue_wave", 0x2060FF)):
        fr = [{"duration_ms": 120, "pixels": "".join("%06x" % (col if (x + y + k) % 3 else 0) for y in range(32) for x in range(32))}
              for k in range(3)]
        json.dump({"name": n, "width": 32, "height": 32, "frames": fr}, open(os.path.join(lib, "Starters", n.replace(" ", "_") + ".json"), "w"))

    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from unicorn_studio import app as A, core

    qa = QtWidgets.QApplication.instance() or QtWidgets.QApplication(["gui_smoke"])
    b = Board(br, kind)
    ctl = A.Controller()
    w = A.MainWindow(ctl)
    w.confirm = lambda q: True
    w.show()
    ctl.connect_broker()

    def pump(cond, secs=10, what="condition"):
        end = time.time() + secs
        while time.time() < end:
            qa.processEvents(QtCore.QEventLoop.AllEvents, 50)
            if cond():
                return True
            time.sleep(0.02)
        raise AssertionError("timeout waiting for " + what + " · status: " + w.status.text())

    def shot(tag):
        if SHOTS:
            os.makedirs(SHOTS, exist_ok=True)
            w.grab().save(os.path.join(SHOTS, "%s-%s.png" % (kind, tag)))

    try:
        print("== GUI", kind, "platform", qa.platformName(), flush=True)
        pump(lambda: ctl.connected, 10, "MQTT connect")
        pump(lambda: b.base in ctl.seen, 15, "board discovered via unicorn/+/state")
        if ctl.base != b.base:          # other (retained) boards on this broker: pick ours in the board menu
            i = w.boards.findData(b.base)
            w.boards.setCurrentIndex(i)
            w.boards.activated.emit(i)
            print("  %d boards on the broker; picked %s in the board menu" % (len(ctl.seen), kind))
        pump(lambda: ctl.connected and ctl.base == b.base and ctl.state.board == kind, 15, "board selected")
        W, H = core.BOARDS[kind]
        assert (ctl.state.w, ctl.state.h) == (W, H)
        assert w.boards.count() >= 1 and w.rot.count() == (4 if W == H else 2)
        print("  connected, board auto-selected from unicorn/+/state:", w.info.text())
        assert "password" not in open(core.settings_path()).read()
        # controls: off/on + brightness
        w.on.click()
        pump(lambda: not ctl.state.on, 8, "board off")
        w.on.click()
        w.bright.setValue(80)
        pump(lambda: ctl.state.on and ctl.state.brightness == 80, 8, "on + brightness 80")
        w.rot.setCurrentText("180")
        w.rot.activated.emit(w.rot.currentIndex())
        pump(lambda: ctl.state.rotation == "180", 8, "rotation")
        w.mode.setCurrentText("starfield")
        [x for x in w.findChildren(QtWidgets.QPushButton) if x.text() == "Play mode"][0].click()
        pump(lambda: ctl.state.mode == "starfield", 8, "mode")
        print("  on/off, brightness, rotation, mode via widgets ok")
        # library: background load, select, explicit Upload (+ play after)
        pump(lambda: len(w.library_files) == 2, 5, "library load")
        w.tabs.setCurrentIndex(2)
        assert w.select_library_file(os.path.join("Starters", "Red_Heart.json"))
        qa.processEvents()
        assert w.cur_disp and ("→ %d×%d" % (W, H) in w.lib_info.text() or (W, H) == (32, 32)), w.lib_info.text()
        assert not [x for x in w.tabs.widget(2).findChildren(QtWidgets.QPushButton) if "playlist" in x.text().lower()], \
            "library must not offer add-to-playlist"
        w.lib_play_after.setChecked(True)
        w.b_send.click()
        assert not w.b_send.isEnabled(), "upload button must be disabled while sending"
        assert not ctl.send_display(w.cur_disp), "second send while busy must be refused"
        pump(lambda: w.status.text().startswith("✓"), 30, "upload confirmed")
        msg = w.status.text()
        pump(lambda: "red_heart" in ctl.state.customs and w.playing_row_text() == "custom:red_heart", 8, "board list + highlight")
        assert w.b_send.isEnabled()
        print("  library Upload:", msg, "· 'On the board' highlights custom:red_heart")
        shot("library")
        w.tabs.setCurrentIndex(0)
        pump(lambda: True, 0.3, "")
        shot("board")

        # ---------- playlist flow: add from On the board -> name -> Save -> Send -> Play
        w.new_playlist()
        assert w.select_board_mode("custom:red_heart")
        w.b_add.click()
        assert w.pl_model.rowCount() == 1 and w.pl_model.item(0, 0).text() == "custom:red_heart"
        assert w.pl_model.item(0, 3).text() == "✓ yes"
        assert w.pl_view.itemDelegateForColumn(2).displayText(w.pl_model.item(0, 2).data(Qt.EditRole), None) == "Normal (10)"
        assert not w.b_pl_send.isEnabled() and not w.b_pl_play.isEnabled(), "Send/Play need a saved playlist"
        assert "not saved yet" in w.pl_status.text(), w.pl_status.text()
        w.add_mode_combo.setCurrentText("clock")
        [x for x in w.findChildren(QtWidgets.QPushButton) if x.text() == "＋ Add mode"][0].click()
        w.add_item("today")
        assert set(w.add_mode_combo.itemText(i) for i in range(w.add_mode_combo.count())) <= set(ctl.state.builtins), "mode combo = built-ins only"
        # a display that is not on the board (e.g. a playlist made earlier) is flagged, never uploaded
        w.add_item("custom:ghost_disp")
        assert w.flagged_rows() == ["custom:ghost_disp"], w.flagged_rows()
        assert "⚠ not on the board: ghost_disp" in w.pl_status.text()
        assert [w.pl_model.item(r, 0).text() for r in range(4)] == ["custom:red_heart", "clock", "today", "custom:ghost_disp"]
        w.tabs.setCurrentIndex(3)
        # seconds: inline editor (real spin box delegate)
        idx = w.pl_model.index(0, 1)
        w.pl_view.setCurrentIndex(idx)
        w.pl_view.edit(idx)
        qa.processEvents()
        sb = w.pl_view.findChild(QtWidgets.QSpinBox)
        assert sb and sb.minimum() == 2 and sb.maximum() == 3600
        sb.setValue(7)
        w.pl_view.commitData(sb)
        w.pl_view.closeEditor(sb, QtWidgets.QAbstractItemDelegate.NoHint)
        pump(lambda: w.pl["items"][0]["seconds"] == 7, 3, "seconds edit")
        w.set_seconds(1, 1)          # clamps to 2
        assert w.pl["items"][1]["seconds"] == 2
        # reorder: drag-and-drop path (same model calls the view makes), then the ↓ button
        m = w.pl_model
        data = m.mimeData([m.index(2, c) for c in range(4)])
        assert m.dropMimeData(data, Qt.MoveAction, 0, 0, QtCore.QModelIndex())
        m.removeRows(3, 1)
        pump(lambda: [i["mode"] for i in w.pl["items"]] == ["today", "custom:red_heart", "clock", "custom:ghost_disp"], 3, "drag reorder")
        w.select_row(0)
        w.move_selected(1)
        assert [i["mode"] for i in w.pl["items"]] == ["custom:red_heart", "today", "clock", "custom:ghost_disp"]
        # Delete key removes the selected row
        w.select_row(1)
        w.pl_view.setFocus()
        QtWidgets.QApplication.sendEvent(w.pl_view, QtGui.QKeyEvent(QtCore.QEvent.KeyPress, Qt.Key_Delete, Qt.NoModifier))
        assert [i["mode"] for i in w.pl["items"]] == ["custom:red_heart", "clock", "custom:ghost_disp"]
        # speed: inline combo editor (1..20, Normal 10)
        idx = w.pl_model.index(0, 2)
        w.pl_view.setCurrentIndex(idx)
        w.pl_view.edit(idx)
        qa.processEvents()
        cb = w.pl_view.findChild(QtWidgets.QComboBox)
        assert cb and cb.count() == 20 and cb.currentData() == 10 and cb.itemText(9) == "Normal (10)"
        cb.setCurrentIndex(cb.findData(15))
        w.pl_view.commitData(cb)
        w.pl_view.closeEditor(cb, QtWidgets.QAbstractItemDelegate.NoHint)
        pump(lambda: w.pl["items"][0].get("speed") == 15, 3, "speed edit")
        w.set_speed(1, 3)
        # Save needs a name
        assert not w.save_playlist() and "name" in w.status.text()
        w.pl_name.setFocus()
        QTest.keyClicks(w.pl_name, "lx Pl!")          # validator: only a-z 0-9 _ - get through
        assert w.pl_name.text() == "lxl", w.pl_name.text()
        QTest.keyClicks(w.pl_name, "_1")
        w.b_pl_save.click()
        name = "lxl_1"
        saved = json.load(open(os.path.join(lib, "Playlists", name + ".json")))
        assert [(i["seconds"], i["speed"]) for i in saved["items"]] == [(7, 15), (2, 3), (10, 10)], saved
        assert all(set(i) == {"mode", "seconds", "speed"} for i in saved["items"]), saved
        assert w.b_pl_send.isEnabled() and not w.b_pl_play.isEnabled(), "after Save: Send yes, Play not yet (not on the board)"
        assert "on the board: no" in w.pl_status.text(), w.pl_status.text()
        print("  playlist editor: add from On the board + built-in modes only, ⚠ flag for ghost_disp, inline seconds + speed, drag + ↑↓,"
              " Delete key, name validated, Save")
        shot("playlist")
        # Send: playlist JSON only; confirmed by the board's state listing it; ghost_disp is NOT uploaded
        customs_before = list(ctl.state.customs)
        w.b_pl_send.click()
        pump(lambda: w.status.text().startswith("✓ playlist") or w.status.text().startswith("✗"), 20, "playlist sent")
        assert w.status.text().startswith("✓ playlist %s is on the board" % name), w.status.text()
        assert "⚠ not on the board" in w.status.text() and "ghost_disp" in w.status.text(), w.status.text()
        assert name in ctl.state.playlists and ctl.state.customs == customs_before and "ghost_disp" not in ctl.state.customs
        assert not os.path.exists(os.path.join(b.flash, "custom", "ghost_disp.bin"))
        onb = json.load(open(os.path.join(b.flash, "playlists", name + ".json")))
        assert [(i["mode"], i["seconds"], i.get("speed")) for i in onb["items"]] == [
            ("custom:red_heart", 7, 15), ("clock", 2, 3), ("custom:ghost_disp", 10, 10)], onb
        pump(lambda: w.b_pl_play.isEnabled(), 3, "Play enabled once the board lists it")
        print("  Send:", w.status.text()[:110])
        # Play: confirmed from board state
        w.b_pl_play.click()
        pump(lambda: w.status.text().startswith("✓ the board is playing") or w.status.text().startswith("✗ the board"), 10, "play")
        assert w.status.text().startswith("✓ the board is playing playlist " + name), w.status.text()
        assert ctl.state.mode == "playlist:" + name
        pump(lambda: w.playing_row_text() in ("playlist:" + name, "custom:red_heart"), 5, "highlight")
        print("  Play:", w.status.text(), "· board", ctl.state.mode, "playing", ctl.state.playing)
        # edit -> must Save before Send; Send does not switch what the board shows
        w.add_item("plasma")
        assert not w.b_pl_send.isEnabled() and "unsaved" in w.pl_status.text()
        ctl.play("clock")
        pump(lambda: ctl.state.mode == "clock", 8, "clock")
        w.b_pl_save.click()
        w.b_pl_send.click()
        pump(lambda: w.status.text().startswith("✓ playlist") or w.status.text().startswith("✗"), 20, "send after edit")
        assert w.status.text().startswith("✓ playlist " + name), w.status.text()
        time.sleep(1)
        pump(lambda: ctl.state.mode == "clock", 4, "still clock")
        assert len(json.load(open(os.path.join(b.flash, "playlists", name + ".json")))["items"]) == 4
        print("  edit -> Save -> Send updated the board copy (4 items) without switching modes")
        # picker shows local vs board playlists; a board-only playlist can be imported
        ctl._publish(b.base + "/playlist/save", json.dumps({"name": "from_board", "items": [{"mode": "clock", "seconds": 5}]}))
        ctl.refresh()
        pump(lambda: "from_board" in ctl.state.playlists, 8, "board-only playlist")
        pump(lambda: w.pl_combo.findData("board:from_board") >= 0, 3, "picker lists board-only playlist")
        labels = [w.pl_combo.itemText(i) for i in range(w.pl_combo.count())]
        assert "%s — this computer + board" % name in labels and "from_board — on the board only" in labels, labels
        ctl._publish(b.base + "/playlist/save", json.dumps({"name": "from_board", "items": [
            {"mode": "clock", "seconds": 5}, {"mode": "custom:ghost_disp", "seconds": 9, "speed": 4}]}))
        time.sleep(0.5)
        w.pick_playlist("board:from_board")
        pump(lambda: w.pl_loaded == "from_board", 8, "import via playlist/get")
        assert os.path.exists(os.path.join(lib, "Playlists", "from_board.json"))
        assert "imported from_board from the board: 2 items" in w.status.text(), w.status.text()
        assert w.pl["items"] == [{"mode": "clock", "seconds": 5}, {"mode": "custom:ghost_disp", "seconds": 9, "speed": 4}], w.pl
        assert w.pl_model.item(1, 3).text().startswith("⚠")          # imported item whose display is not on the board
        labels = [w.pl_combo.itemText(i) for i in range(w.pl_combo.count())]
        assert "from_board — this computer + board" in labels, labels
        w.select_playlist(name)
        print("  picker:", " | ".join(labels), "· board-only playlist imported with its items (playlist/get)")
        # board list: delete display, refresh button; the playlist item is then flagged
        w.tabs.setCurrentIndex(0)
        pump(lambda: w.select_board_mode("custom:red_heart"), 3, "row")
        w.b_del.click()
        pump(lambda: "red_heart" not in ctl.state.customs, 8, "delete from board")
        t0 = ctl.state.at
        w.b_refresh.click()
        pump(lambda: ctl.state.at > t0, 8, "refresh")
        pump(lambda: "custom:red_heart" in w.flagged_rows(), 3, "flag after delete")
        # board list: play a playlist row, delete a playlist row via Delete key
        pump(lambda: w.select_board_mode("playlist:" + name), 3, "pl row")
        w.b_play.click()
        pump(lambda: ctl.state.mode == "playlist:" + name, 8, "play playlist row")
        w.blist.setFocus()
        w.select_board_mode("playlist:" + name)
        QtWidgets.QApplication.sendEvent(w.blist, QtGui.QKeyEvent(QtCore.QEvent.KeyPress, Qt.Key_Delete, Qt.NoModifier))
        pump(lambda: name not in ctl.state.playlists, 8, "delete playlist from board")
        pump(lambda: not w.b_pl_play.isEnabled(), 3, "Play disabled once gone from the board")
        print("  board list: Delete, Refresh, Play playlist, Delete key ok; deleted display now flagged in the playlist")

        # ---------- pixel editor: draw, frames, undo, save, reload, upload (byte-exact), live mirror
        w.tabs.setCurrentIndex(1)
        pump(lambda: (w.ed.w, w.ed.h) == (W, H), 3, "canvas follows the board")
        cv = w.canvas

        def mouse(kind_, x, y):
            pt = cv.cell_center(x, y)
            t = {"press": QtCore.QEvent.MouseButtonPress, "move": QtCore.QEvent.MouseMove, "release": QtCore.QEvent.MouseButtonRelease}[kind_]
            btns = Qt.NoButton if kind_ == "release" else Qt.LeftButton
            ev = QtGui.QMouseEvent(t, pt, cv.mapToGlobal(pt.toPoint()), Qt.LeftButton, btns, Qt.NoModifier)
            QtWidgets.QApplication.sendEvent(cv, ev)

        def stroke(pts):
            mouse("press", *pts[0])
            for p_ in pts[1:]:
                mouse("move", *p_)
            mouse("release", *pts[-1])
            qa.processEvents()
        ed = w.ed
        w.use_colour(0x00FF00)
        w.set_tool("pencil")
        stroke([(0, 0), (5, 0)])                                # one drag = Bresenham line
        assert all(ed.px[x] == 0x00FF00 for x in range(6)) and ed.px[6] == 0
        w.set_tool("eraser")
        stroke([(2, 0)])
        assert ed.px[2] == 0
        w.use_colour(0xFF0000)
        w.set_tool("fill")
        stroke([(W - 1, H - 1)])
        filled = sum(1 for v in ed.px if v == 0xFF0000)
        assert filled == W * H - 5, filled
        w.editor_undo()
        assert sum(1 for v in ed.px if v == 0xFF0000) == 0, "undo the fill"
        w.set_tool("picker")
        stroke([(0, 0)])
        assert ed.color == 0x00FF00 and ed.recent[0] == 0x00FF00
        w.set_tool("text")
        w.stamp_text.setText("A")
        stroke([(0, 2)])
        assert ed.px[2 * W + 1] == 0x00FF00 and ed.px[3 * W + 0] == 0x00FF00   # 'A' glyph
        # frames: add, duplicate, duration, move, delete, onion, playback
        w._ed(ed.dup_frame)
        w.set_tool("pencil")
        w.use_colour(0x2060FF)
        stroke([(W - 1, 0), (W - 1, H - 1)])
        w.dur.setValue(120)
        w.dur.editingFinished.emit()
        w._ed(ed.add_frame)
        stroke([(1, H - 1)])
        assert len(ed.frames) == 3 and ed.cur == 2 and w.strip.count() == 3
        assert [d for _, d in ed.frames] == [200, 120, 120], [d for _, d in ed.frames]
        w._ed(lambda: ed.move_frame(-1))
        assert ed.cur == 1 and ed.frames[1][0][(H - 1) * W + 1] == 0x2060FF
        w._ed(lambda: ed.move_frame(1))
        w._ed(ed.add_frame)
        w._ed(ed.del_frame)
        assert len(ed.frames) == 3
        w.onion.setChecked(True)
        w.toggle_anim()
        seen_frames = set()
        pump(lambda: seen_frames.add(ed.cur) or len(seen_frames) == 3, 3, "preview playback")
        w.toggle_anim()
        print("  editor: pencil line, eraser, fill + undo, eyedropper, text stamp, add/duplicate/move/delete frames,"
              " per-frame ms, onion skin, playback")
        shot("editor")
        # save, reload, compare
        w.ed_name.setText("")
        QTest.keyClicks(w.ed_name, "lx_draw")
        path = w.editor_save()
        assert path and os.path.basename(path) == "lx_draw.json"
        d = core.load_display(path)
        assert (d.w, d.h) == (W, H) and [(list(p_), dd) for p_, dd in d.frames] == [(p_, dd) for p_, dd in ed.frames]
        assert json.load(open(path)).get("format") == "unicorn-display"
        frames_saved = [list(p_) for p_, _ in ed.frames]
        w.editor_new()
        assert len(ed.frames) == 1 and not any(ed.px)
        w.tabs.setCurrentIndex(2)
        pump(lambda: "lx_draw.json" in w.library_files, 5, "library lists the saved display")
        assert w.select_library_file("lx_draw.json")
        qa.processEvents()
        w.open_selected_in_editor()
        assert w.tabs.currentIndex() == 1 and [list(p_) for p_, _ in ed.frames] == frames_saved
        print("  saved lx_draw.json (standard format), reloaded from the library: identical")
        # upload from the editor, byte-exact on the simulated flash
        w.ed_play_after.setChecked(True)
        w.b_ed_up.click()
        pump(lambda: w.status.text().startswith("✓") or w.status.text().startswith("✗"), 30, "editor upload")
        assert w.status.text().startswith("✓"), w.status.text()
        stored = open(os.path.join(b.flash, "custom", "lx_draw.bin"), "rb").read()
        assert stored == b"".join(core.rgb_bytes(p_) for p_ in frames_saved), "stored bytes differ"
        pump(lambda: "lx_draw" in ctl.state.customs and ctl.state.mode == "custom:lx_draw", 8, "board shows lx_draw")
        print("  editor Upload:", w.status.text(), "· byte-exact on the board's flash (%d bytes)" % len(stored))
        # live mirror
        pump(lambda: w.live.isEnabled(), 3, "live available")
        w.live.setChecked(True)
        pump(lambda: ctl.state.mode == "live", 8, "board in live mode")
        stroke([(3, 3), (4, 4)])
        time.sleep(0.4)
        qa.processEvents()
        w.live.setChecked(False)
        pump(lambda: ctl.state.mode != "live", 8, "live off")
        print("  live mirror: board reports mode 'live' while on, back to", ctl.state.mode, "when off")
        # canvas of another size: live is disabled, upload scales
        other = [k for k in core.BOARDS if core.BOARDS[k] != (W, H)][0]
        i = w.canvas_combo.findData(other)
        w.canvas_combo.setCurrentIndex(i)
        w.canvas_combo.activated.emit(i)
        assert (ed.w, ed.h) == core.BOARDS[other], (ed.w, ed.h)
        assert not w.live.isEnabled(), "live must be off for a canvas of another size"
        assert json.load(open(core.settings_path())).get("canvas") == other, open(core.settings_path()).read()
        i = w.canvas_combo.findData("auto")
        w.canvas_combo.setCurrentIndex(i)
        w.canvas_combo.activated.emit(i)
        assert (ed.w, ed.h) == (W, H)
        print("  canvas picker: %s %dx%d disables live; 'Match the board' returns to %dx%d" % ((other,) + core.BOARDS[other] + (W, H)))
        # failure is shown as failure: board goes away mid-session
        b.kill()
        w.tabs.setCurrentIndex(2)
        assert w.select_library_file(os.path.join("Starters", "Red_Heart.json"))
        qa.processEvents()
        ctl.up.first_wait, ctl.up.retry_wait = 1.0, 0.5
        w.b_send.click()
        pump(lambda: w.status.text().startswith("✗"), 15, "failure shown")
        print("  board offline -> upload reported as failure:", w.status.text()[:70])
        # import a PNG
        img = QtGui.QImage(64, 40, QtGui.QImage.Format_RGB32)
        img.fill(QtGui.QColor(250, 200, 0))
        png = os.path.join(home, "sun.png")
        img.save(png)
        rel = w.import_paths([png])
        d = core.load_display(os.path.join(lib, rel))
        assert (d.w, d.h) == (W, H) and any(d.frames[0][0])
        print("  PNG import ->", rel, "%dx%d" % (d.w, d.h))
        # settings dialog never writes a password to disk
        dlg = A.SettingsDialog(ctl, w)
        assert dlg.pw.echoMode() == QtWidgets.QLineEdit.Password
        c = dlg.values()
        c.save(remember_password=True)
        assert br.password not in open(core.settings_path()).read()
        dlg.deleteLater()
        print("  settings dialog: password masked, not written to settings.json")
        print("  PASS GUI", kind, flush=True)
    finally:
        w.close()
        ctl.stop()
        b.stop()
        qa.processEvents()


def main():
    br = Broker()
    ok = True
    try:
        for k in sys.argv[1:] or ["cosmic", "galactic", "stellar"]:
            try:
                run(k, br)
            except AssertionError as e:
                ok = False
                print("  FAIL GUI", k, e, flush=True)
    finally:
        br.stop()
    print("ALL PASS" if ok else "SOME FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
