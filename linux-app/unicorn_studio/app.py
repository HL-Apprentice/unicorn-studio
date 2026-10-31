"""Unicorn Studio for Linux (Qt / PySide6). Board control, the 'On the board' list, library, uploads, playlists."""
import argparse, json, os, shutil, sys, threading, time

from . import VERSION, core, mqtt

try:
    from PySide6 import QtCore, QtGui, QtWidgets
    from PySide6.QtCore import Qt, Signal
except ImportError:                       # allow --version / --check-config without Qt
    QtCore = QtGui = QtWidgets = None

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
BUNDLED_ANIMATIONS = os.path.join(HERE, "animations")


def _log(*a):
    print("unicorn-studio:", *a, file=sys.stderr, flush=True)


if QtWidgets is not None:

    # ------------------------------------------------------------------ controller (no widgets)
    class Controller(QtCore.QObject):
        conn_changed = Signal(bool, str)
        state_changed = Signal()
        seen_changed = Signal()
        log = Signal(str, object)              # message, ok (True/False/None)
        busy_changed = Signal(bool)
        _msg = Signal(str, object)
        _go_refresh = Signal()
        _upload_finished = Signal()
        playlist_items = Signal(str, object, str)   # name, items or None, error

        def __init__(self, cfg=None):
            super().__init__()
            self.cfg = cfg or core.ConnConfig.load()
            self.state = core.BoardState()
            self.seen = {}                      # base -> (board, w, h, version)
            self.connected = False
            self.sending = False
            self.mq = None
            self.up = None
            self.plw = core.PlaylistWaiter()
            self.itw = core.ItemsWaiter()
            self._refresh_pending = False
            self._msg.connect(self._ui_message)                 # queued: emitted on the MQTT thread
            self._go_refresh.connect(self.refresh)
            self._upload_finished.connect(self._upload_done)

        @property
        def base(self):
            return self.cfg.base

        def lib(self):
            return core.library_dir(self.cfg.library)

        # ---- connection
        def connect_broker(self):
            if self.mq:
                self.mq.stop()
                self.mq = None
            self.connected = False
            if not self.cfg.complete:
                self.conn_changed.emit(False, "open Settings to enter the MQTT broker")
                return
            subs = ["unicorn/+/state"]
            if self.base:
                self.up = core.Uploader(self._publish, self.base)
                b = self.base
                subs += [b + "/state", b + "/status", b + "/custom/ack", b + "/custom/result", b + "/custom/result2",
                         b + "/playlist/result", b + "/playlist/result2", b + "/playlist/items"]
            self.mq = mqtt.Client("unicorn-studio-linux-%s" % os.urandom(3).hex())
            self.mq.on_message = self._on_message
            self.mq.on_connect = self._on_connect
            self.conn_changed.emit(False, "connecting…")
            self.mq.start(self.cfg.host, self.cfg.port, self.cfg.user, self.cfg.password, self.cfg.tls, subs)

        def stop(self):
            if self.mq:
                self.mq.stop()

        def _on_connect(self, ok, why):          # reader thread
            self.connected = ok
            self.conn_changed.emit(ok, "connected to %s:%d" % (self.cfg.host, self.cfg.port) if ok else "offline: " + why)
            if ok:
                self._go_refresh.emit()                              # auto-refresh on every (re)connect

        def _publish(self, topic, payload):
            if not self.mq:
                raise mqtt.MQTTError("not connected")
            self.mq.publish(topic, payload)

        def _on_message(self, t, p):             # reader thread
            b = self.base
            if b and t.startswith(b + "/custom/") and self.up:
                self.up.feed(t, p)
            if b and t == b + "/playlist/result":
                self.plw.feed(p)
            elif b and t == b + "/playlist/result2":
                self.plw.feed2(p)
            elif b and t == b + "/playlist/items":
                self.itw.feed(p)
            self._msg.emit(t, bytes(p))

        def _ui_message(self, t, p):
            b = self.base
            if t.startswith("unicorn/") and t.endswith("/state"):
                self._note_seen(t, p)
            if b and t == b + "/state":
                if self.state.apply(p):
                    if self.up:
                        self.up.v2 = self.state.v2
                    self._refresh_pending = False
                    self.state_changed.emit()
            elif b and t == b + "/status":
                self.state.status = p[:20].decode("utf-8", "replace")
                self.state_changed.emit()
            elif b and t == b + "/playlist/result" and not self.sending:
                self.log.emit("board: " + p[:200].decode("utf-8", "replace"), None)

        def _note_seen(self, t, p):
            bid = t[len("unicorn/"):-len("/state")]
            if not bid or "/" in bid:
                return
            try:
                j = json.loads(p)
            except ValueError:
                return
            if not isinstance(j, dict):
                return
            self.seen["unicorn/" + bid] = (str(j.get("board", "?")), j.get("width", 32), j.get("height", 32), str(j.get("version", "?")))
            self.seen_changed.emit()
            if not self.base and len(self.seen) == 1:                  # first board found: use it
                self.select_board("unicorn/" + bid)

        def select_board(self, base):
            if base == self.cfg.base:
                return
            self.cfg.base = base
            self.cfg.save()
            self.state.reset()
            self.state_changed.emit()
            self.connect_broker()

        # ---- commands
        def cmd(self, k, v):
            if not self.base or not self.connected:
                self.log.emit("not connected", False)
                return False
            try:
                self._publish(self.base + "/" + k + "/set", v)
                return True
            except Exception as e:
                self.log.emit("send failed: %s" % e, False)
                return False

        def light(self, **kw):
            return self.cmd("light", json.dumps(kw))

        def refresh(self):
            if not (self.connected and self.base):
                return
            self._refresh_pending = True
            self.cmd("refresh", "1")
            QtCore.QTimer.singleShot(5000, self, self._refresh_timeout)

        def _refresh_timeout(self):
            if self._refresh_pending and time.time() - self.state.at > 5:
                self._refresh_pending = False
                self.log.emit("no answer from the board (offline, or older firmware)", False)

        def play(self, mode):
            if self.cmd("mode", mode):
                self.state.mode = mode
                self.state_changed.emit()
                QtCore.QTimer.singleShot(800, self, self.refresh)

        def delete_custom(self, name):
            try:
                self._publish(self.base + "/custom/delete", name)
                self.log.emit("asked the board to delete %s" % name, None)
            except Exception as e:
                self.log.emit("delete failed: %s" % e, False)
            QtCore.QTimer.singleShot(800, self, self.refresh)

        def delete_playlist_on_board(self, name):
            try:
                self._publish(self.base + "/playlist/delete", name)
                self.log.emit("asked the board to delete playlist %s" % name, None)
            except Exception as e:
                self.log.emit("delete failed: %s" % e, False)
            QtCore.QTimer.singleShot(800, self, self.refresh)

        # ---- uploads (worker thread, single-flight)
        def for_board(self, disp, fit):
            return disp.resized(self.state.w, self.state.h, fit) if self.state.w else disp

        def _set_sending(self, v):
            self.sending = v
            self.busy_changed.emit(v)

        def send_display(self, disp, fit="fit", play=True, done=None):
            if self.sending:
                self.log.emit("an upload is already running", False)
                return False
            if not (self.up and self.connected):
                self.log.emit("not connected to MQTT", False)
                return False
            d = self.for_board(disp, fit)
            name = core.board_name(d.name)
            self._set_sending(True)
            self.log.emit("sending %s…" % name, None)

            def work():
                r = self.up.send(name, d.frames, play=play,
                                 progress=lambda i, n: self.log.emit("sending %s: frame %d/%d confirmed" % (name, i, n), None))
                extra = " (board keeps the first %d frames)" % core.MAX_FRAMES if len(d.frames) > core.MAX_FRAMES else ""
                self.log.emit(("✓ " if r.ok else "✗ Not saved: ") + r.msg + extra, r.ok)
                self._upload_finished.emit()
                if done:
                    done(r)
            threading.Thread(target=work, name="upload", daemon=True).start()
            return True

        def _upload_done(self):
            self._set_sending(False)
            self.refresh()

        def send_playlist(self, pl, done=None):
            """Send ONLY the playlist JSON (never uploads displays). Success = the board says ok AND its state lists it."""
            if self.sending:
                self.log.emit("an upload is already running", False)
                return False
            if not (self.up and self.connected):
                self.log.emit("not connected to MQTT", False)
                return False
            if not self.state.v2:
                self.log.emit("playlists need the us-2 firmware on the board", False)
                return False
            if not pl["items"]:
                self.log.emit("the playlist is empty", False)
                return False
            name = pl["name"]
            missing = core.missing_on_board(pl, self.state.customs)
            uid = os.urandom(4).hex() if self.state.has_get else None     # us-2.1 echoes it in playlist/result2
            payload = core.playlist_board_json(pl, uid)
            self._set_sending(True)
            self.log.emit("sending playlist %s to the board (playlist only, no displays)…" % name, None)

            def work():
                ok = False
                try:
                    self.plw.arm(name, uid)
                    t0 = time.time()
                    try:
                        self._publish(self.base + "/playlist/save", payload)
                    except Exception as e:
                        self.log.emit("✗ playlist not sent: %s" % e, False)
                        return
                    reply = self.plw.wait(6.0)
                    if not reply or not reply.startswith("ok playlist"):
                        self.log.emit("✗ board did not save playlist %s: %s" % (name, reply or "no answer within 6 s"), False)
                        return
                    try:
                        self._publish(self.base + "/refresh/set", "1")
                    except Exception:
                        pass
                    end = time.time() + 6
                    while time.time() < end and not (self.state.at >= t0 and name in self.state.playlists):
                        time.sleep(0.1)
                    if name not in self.state.playlists:
                        self.log.emit("✗ board replied (%s) but its playlist list does not show %s" % (reply, name), False)
                        return
                    ok = True
                    msg = "✓ playlist %s is on the board (%s)" % (name, reply)
                    if missing:
                        self.log.emit(msg + " · ⚠ not on the board, skipped until you upload them: " + ", ".join(missing), "warn")
                    else:
                        self.log.emit(msg + " · press Play to start it", True)
                finally:
                    self._upload_finished.emit()
                    if done:
                        done(ok)
            threading.Thread(target=work, name="playlist", daemon=True).start()
            return True

        def fetch_playlist(self, name):
            """us-2.1: ask the board for a stored playlist's items (playlist/get -> playlist/items)."""
            if not (self.connected and self.base):
                self.playlist_items.emit(name, None, "not connected")
                return

            def work():
                self.itw.arm(name)
                try:
                    self._publish(self.base + "/playlist/get", name)
                except Exception as e:
                    self.itw.wait(0)
                    self.playlist_items.emit(name, None, str(e))
                    return
                items, err = self.itw.wait(5.0)
                self.playlist_items.emit(name, items, err or "")
            threading.Thread(target=work, name="playlist-get", daemon=True).start()

        # ---- live mirror: the board shows the editor canvas while live is on (custom/live, not stored)
        def live_frame(self, px):
            if self.sending or not (self.connected and self.base):
                return False
            try:
                for c in core.live_payloads(px):
                    self._publish(self.base + "/custom/live", c)
                return True
            except Exception as e:
                self.log.emit("live mirror stopped: %s" % e, False)
                return False

        def live_off(self):
            try:
                if self.connected and self.base:
                    self._publish(self.base + "/custom/live", b"")
            except Exception:
                pass

        def play_playlist(self, name, done=None):
            """Start a playlist that is on the board; confirmed when the board's state says it is playing it."""
            if name not in self.state.playlists:
                self.log.emit("playlist %s is not on the board yet: Send it first" % name, False)
                return False
            t0 = time.time()
            if not self.cmd("mode", "playlist:" + name):
                return False
            self.log.emit("starting playlist %s…" % name, None)

            def work():
                try:
                    self._publish(self.base + "/refresh/set", "1")
                except Exception:
                    pass
                end = time.time() + 6
                while time.time() < end and not (self.state.at >= t0 and self.state.mode == "playlist:" + name):
                    time.sleep(0.1)
                ok = self.state.mode == "playlist:" + name
                self.log.emit(("✓ the board is playing playlist %s" % name) if ok else
                              ("✗ the board did not switch to playlist %s" % name), ok)
                if done:
                    done(ok)
            threading.Thread(target=work, name="play", daemon=True).start()
            return True

    class PixelView(QtWidgets.QWidget):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.disp = None
            self.i = 0
            self.setMinimumSize(200, 120)
            self.timer = QtCore.QTimer(self)
            self.timer.timeout.connect(self._next)

        def set_display(self, d):
            self.disp, self.i = d, 0
            self.timer.stop()
            if d and len(d.frames) > 1:
                self.timer.start(d.frames[0][1])
            self.update()

        def _next(self):
            if not self.disp:
                return
            self.i = (self.i + 1) % len(self.disp.frames)
            self.timer.start(self.disp.frames[self.i][1])
            self.update()

        def paintEvent(self, _e):
            p = QtGui.QPainter(self)
            p.fillRect(self.rect(), QtGui.QColor(13, 13, 13))
            d = self.disp
            if not d:
                return
            c = min(self.width() / d.w, self.height() / d.h)
            ox, oy = (self.width() - c * d.w) / 2, (self.height() - c * d.h) / 2
            px = d.frames[self.i][0]
            for k, v in enumerate(px):
                if v:
                    p.fillRect(QtCore.QRectF(ox + (k % d.w) * c, oy + (k // d.w) * c, c, c), QtGui.QColor((v >> 16) & 255, (v >> 8) & 255, v & 255))

    def _qc(v):
        return QtGui.QColor((v >> 16) & 255, (v >> 8) & 255, v & 255)

    class Canvas(QtWidgets.QWidget):
        """The editor grid. Left button draws with the current tool; the stroke is one undo step."""
        changed = Signal()
        picked = Signal()

        def __init__(self, ed, parent=None):
            super().__init__(parent)
            self.ed = ed
            self.tool = "pencil"
            self.text = "HI"
            self.onion = False
            self.last = None
            self.setMinimumSize(320, 200)
            self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
            self.setCursor(Qt.CrossCursor)

        def geom(self):
            e = self.ed
            c = max(1.0, min(self.width() / e.w, self.height() / e.h))
            return c, (self.width() - c * e.w) / 2, (self.height() - c * e.h) / 2

        def cell_at(self, pos):
            c, ox, oy = self.geom()
            return int((pos.x() - ox) // c), int((pos.y() - oy) // c)

        def cell_center(self, x, y):
            c, ox, oy = self.geom()
            return QtCore.QPointF(ox + (x + 0.5) * c, oy + (y + 0.5) * c)

        def paintEvent(self, _e):
            e = self.ed
            p = QtGui.QPainter(self)
            p.fillRect(self.rect(), self.palette().window())
            c, ox, oy = self.geom()
            p.fillRect(QtCore.QRectF(ox, oy, c * e.w, c * e.h), QtGui.QColor(13, 13, 13))
            ghost = e.frames[e.cur - 1][0] if self.onion and e.cur > 0 else None
            for k, v in enumerate(e.px):
                r = QtCore.QRectF(ox + (k % e.w) * c, oy + (k // e.w) * c, c, c)
                if v:
                    p.fillRect(r, _qc(v))
                elif ghost and ghost[k]:
                    g = _qc(ghost[k])
                    g.setAlpha(64)
                    p.fillRect(r, g)
            if c >= 6:
                p.setPen(QtGui.QColor(60, 60, 60))
                for x in range(e.w + 1):
                    p.drawLine(QtCore.QPointF(ox + x * c, oy), QtCore.QPointF(ox + x * c, oy + e.h * c))
                for y in range(e.h + 1):
                    p.drawLine(QtCore.QPointF(ox, oy + y * c), QtCore.QPointF(ox + e.w * c, oy + y * c))

        def mousePressEvent(self, ev):
            if ev.button() == Qt.LeftButton:
                self.last = None
                self._apply(ev.position() if hasattr(ev, "position") else ev.pos(), True)

        def mouseMoveEvent(self, ev):
            if ev.buttons() & Qt.LeftButton:
                self._apply(ev.position() if hasattr(ev, "position") else ev.pos(), False)

        def mouseReleaseEvent(self, _ev):
            self.last = None

        def _apply(self, pos, first):
            e = self.ed
            x, y = self.cell_at(pos)
            if not (0 <= x < e.w and 0 <= y < e.h):
                return
            if (x, y) == self.last:
                return
            if self.tool == "picker":
                e.pick(x, y)
                self.picked.emit()
                self.last = (x, y)
                return
            if first:
                e.snapshot()
            if self.tool in ("pencil", "eraser"):
                col = 0 if self.tool == "eraser" else e.color
                if self.last:
                    e.line(self.last, (x, y), col)
                else:
                    e.set(x, y, col)
            elif self.tool == "fill" and first:
                e.fill(x, y, e.color)
            elif self.tool == "text" and first:
                e.stamp(x, y, self.text)
            self.last = (x, y)
            self.update()
            self.changed.emit()

    def frame_icon(px, w, h, size=56):
        img = QtGui.QImage(w, h, QtGui.QImage.Format_RGB32)
        for k, v in enumerate(px):
            img.setPixel(k % w, k // w, 0xFF000000 | (v or 0x0D0D0D))
        return QtGui.QIcon(QtGui.QPixmap.fromImage(img.scaled(size, max(8, int(size * h / w)), Qt.KeepAspectRatio)))

    class SecondsDelegate(QtWidgets.QStyledItemDelegate):
        def createEditor(self, parent, option, index):
            sb = QtWidgets.QSpinBox(parent)
            sb.setRange(core.SECONDS_MIN, core.SECONDS_MAX)
            sb.setSuffix(" s")
            return sb

        def setEditorData(self, ed, index):
            ed.setValue(core.clamp_seconds(index.data(Qt.EditRole)))

        def setModelData(self, ed, model, index):
            ed.interpretText()
            model.setData(index, ed.value(), Qt.EditRole)

    def speed_text(v):
        if v is None:
            return "board speed"
        return "Normal (10)" if v == core.SPEED_NORMAL else "%d%s" % (v, " (slowest)" if v == 1 else " (fastest)" if v == 20 else "")

    class SpeedDelegate(QtWidgets.QStyledItemDelegate):
        """Per-item speed 1..20; 10 = Normal (the display's own frame timing)."""
        def displayText(self, value, locale):
            return speed_text(None if value in (None, "", 0) else int(value))

        def createEditor(self, parent, option, index):
            cb = QtWidgets.QComboBox(parent)
            for v in range(core.SPEED_MIN, core.SPEED_MAX + 1):
                cb.addItem(speed_text(v), v)
            return cb

        def setEditorData(self, ed, index):
            v = index.data(Qt.EditRole)
            ed.setCurrentIndex(max(0, ed.findData(int(v) if v else core.SPEED_NORMAL)))

        def setModelData(self, ed, model, index):
            model.setData(index, ed.currentData(), Qt.EditRole)

    class PlaylistView(QtWidgets.QTreeView):
        delete_pressed = Signal()

        def keyPressEvent(self, e):
            if e.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.state() != QtWidgets.QAbstractItemView.EditingState:
                self.delete_pressed.emit()
                return
            super().keyPressEvent(e)

    class BoardList(QtWidgets.QListWidget):
        delete_pressed = Signal()

        def keyPressEvent(self, e):
            if e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
                self.delete_pressed.emit()
                return
            super().keyPressEvent(e)

    class SettingsDialog(QtWidgets.QDialog):
        def __init__(self, ctl, parent=None):
            super().__init__(parent)
            self.setWindowTitle("Unicorn Studio settings")
            c = ctl.cfg
            f = QtWidgets.QFormLayout(self)
            self.host = QtWidgets.QLineEdit(c.host)
            self.host.setPlaceholderText("homeassistant.local")
            self.port = QtWidgets.QSpinBox()
            self.port.setRange(1, 65535)
            self.port.setValue(c.port)
            self.user = QtWidgets.QLineEdit(c.user)
            self.pw = QtWidgets.QLineEdit(c.password)
            self.pw.setEchoMode(QtWidgets.QLineEdit.Password)
            self.tls = QtWidgets.QCheckBox("Use TLS")
            self.tls.setChecked(c.tls)
            self.board = QtWidgets.QComboBox()
            self.board.setEditable(True)
            self.board.addItems(sorted(set(list(ctl.seen) + ([c.base] if c.base else []))))
            self.board.setCurrentText(c.base)
            self.board.lineEdit().setPlaceholderText("unicorn/<id>  (empty = first board found)")
            self.lib = QtWidgets.QLineEdit(c.library)
            self.lib.setPlaceholderText(core.library_dir(""))
            browse = QtWidgets.QPushButton("Browse…")
            browse.clicked.connect(self._browse)
            lrow = QtWidgets.QHBoxLayout()
            lrow.addWidget(self.lib)
            lrow.addWidget(browse)
            self.remember = QtWidgets.QCheckBox("Remember password in the system keyring")
            if not core.keyring_available():
                self.remember.setEnabled(False)
                self.remember.setToolTip("Install the optional 'keyring' package, or put MQTT_PASSWORD in "
                                         "~/.config/unicorn-studio/config.env (chmod 600) or the environment.")
            note = QtWidgets.QLabel("The password is never written to settings.json. Without the keyring it is kept for "
                                    "this session only, unless it comes from config.env or the environment.")
            note.setWordWrap(True)
            note.setStyleSheet("color: gray")
            f.addRow("MQTT broker", self.host)
            f.addRow("Port", self.port)
            f.addRow("User", self.user)
            f.addRow("Password", self.pw)
            f.addRow("", self.tls)
            f.addRow("Board", self.board)
            f.addRow("Library folder", lrow)
            f.addRow("", self.remember)
            f.addRow(note)
            bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
            bb.accepted.connect(self.accept)
            bb.rejected.connect(self.reject)
            f.addRow(bb)

        def _browse(self):
            d = QtWidgets.QFileDialog.getExistingDirectory(self, "Library folder", self.lib.text() or self.lib.placeholderText())
            if d:
                self.lib.setText(d)

        def values(self):
            return core.ConnConfig(self.host.text().strip(), self.port.value(), self.user.text().strip(), self.pw.text(),
                                   self.board.currentText().strip(), self.tls.isChecked(), self.lib.text().strip())

    class MainWindow(QtWidgets.QMainWindow):
        _lib_ready = Signal(object)

        def __init__(self, ctl):
            super().__init__()
            self._lib_ready.connect(self._library_loaded)
            self.ctl = ctl
            self.setWindowTitle("Unicorn Studio")
            self.resize(980, 680)
            icon = os.path.join(DATA, "unicorn-studio.svg")
            if os.path.exists(icon):
                self.setWindowIcon(QtGui.QIcon(icon))
            self.library_files = []
            self.cur_disp = None
            self.pl = {"name": "", "items": []}
            self.pl_loaded = None                   # name of the local file the editor holds (None = new)
            self.pl_dirty = False
            self._loading_pl = False
            self.confirm = self._confirm            # replaceable in tests

            central = QtWidgets.QWidget()
            v = QtWidgets.QVBoxLayout(central)
            top = QtWidgets.QHBoxLayout()
            self.conn = QtWidgets.QLabel("…")
            self.boards = QtWidgets.QComboBox()
            self.boards.setMinimumWidth(220)
            self.boards.activated.connect(lambda i: self.ctl.select_board(self.boards.itemData(i)))
            b_set = QtWidgets.QPushButton("Settings…")
            b_set.clicked.connect(self.open_settings)
            top.addWidget(self.conn, 1)
            top.addWidget(QtWidgets.QLabel("Board:"))
            top.addWidget(self.boards)
            top.addWidget(b_set)
            v.addLayout(top)
            self.tabs = QtWidgets.QTabWidget()
            self.ed = core.Editor(*core.BOARDS.get(self._saved_canvas(), (32, 32)))
            self.tabs.addTab(self._board_tab(), "Board")
            self.tabs.addTab(self._editor_tab(), "Editor")
            self.tabs.addTab(self._library_tab(), "Library")
            self.tabs.addTab(self._playlist_tab(), "Playlists")
            v.addWidget(self.tabs, 1)
            self.status = QtWidgets.QLabel("")
            self.status.setWordWrap(True)
            self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
            v.addWidget(self.status)
            self.setCentralWidget(central)

            ctl.conn_changed.connect(self._conn)
            ctl.state_changed.connect(self._state)
            ctl.seen_changed.connect(self._seen)
            ctl.log.connect(self._log)
            ctl.playlist_items.connect(self._playlist_items)
            ctl.busy_changed.connect(self._busy)
            self.reload_library()
            self.refresh_playlists()
            QtGui.QShortcut(QtGui.QKeySequence.Refresh, self, activated=self.ctl.refresh)

        # ---------- board tab
        def _board_tab(self):
            w = QtWidgets.QWidget()
            g = QtWidgets.QGridLayout(w)
            self.info = QtWidgets.QLabel("no board yet")
            g.addWidget(self.info, 0, 0, 1, 4)
            self.on = QtWidgets.QCheckBox("On")
            self.on.clicked.connect(lambda c: self.ctl.light(state="ON" if c else "OFF"))
            self.bright = QtWidgets.QSlider(Qt.Horizontal)
            self.bright.setRange(0, 100)
            self.bright.sliderReleased.connect(self._bright)
            self.bright.valueChanged.connect(lambda _: None if self.bright.isSliderDown() else self._bright_debounce.start())
            self._bright_debounce = QtCore.QTimer(self, singleShot=True, interval=250, timeout=self._bright)
            g.addWidget(self.on, 1, 0)
            g.addWidget(QtWidgets.QLabel("Brightness"), 1, 1)
            g.addWidget(self.bright, 1, 2, 1, 2)
            self.mode = QtWidgets.QComboBox()
            b_mode = QtWidgets.QPushButton("Play mode")
            b_mode.clicked.connect(lambda: self.mode.currentText() and self.ctl.play(self.mode.currentText()))
            self.rot = QtWidgets.QComboBox()
            self.rot.activated.connect(lambda i: self.ctl.cmd("rotation", self.rot.itemText(i)))
            g.addWidget(QtWidgets.QLabel("Mode"), 2, 0)
            g.addWidget(self.mode, 2, 1, 1, 2)
            g.addWidget(b_mode, 2, 3)
            g.addWidget(QtWidgets.QLabel("Rotation"), 3, 0)
            g.addWidget(self.rot, 3, 1)
            self.text = QtWidgets.QLineEdit()
            self.text.setMaxLength(255)
            self.text.setPlaceholderText("scrolling text")
            b_text = QtWidgets.QPushButton("Show text")
            b_text.clicked.connect(lambda: self.ctl.cmd("text", self.text.text()))
            g.addWidget(QtWidgets.QLabel("Text"), 4, 0)
            g.addWidget(self.text, 4, 1, 1, 2)
            g.addWidget(b_text, 4, 3)
            self.speed = QtWidgets.QSlider(Qt.Horizontal)
            self.speed.setRange(1, 20)
            self.speed.sliderReleased.connect(lambda: self.ctl.cmd("speed", str(self.speed.value())))
            g.addWidget(QtWidgets.QLabel("Speed"), 5, 0)
            g.addWidget(self.speed, 5, 1, 1, 3)

            box = QtWidgets.QGroupBox("On the board")
            bl = QtWidgets.QVBoxLayout(box)
            self.blist = BoardList()
            self.blist.setContextMenuPolicy(Qt.CustomContextMenu)
            self.blist.customContextMenuRequested.connect(self._board_menu)
            self.blist.itemDoubleClicked.connect(lambda _: self.board_play())
            self.blist.delete_pressed.connect(self.board_delete)
            bl.addWidget(self.blist)
            row = QtWidgets.QHBoxLayout()
            self.b_play = QtWidgets.QPushButton("▶ Play")
            self.b_play.clicked.connect(self.board_play)
            self.b_add = QtWidgets.QPushButton("＋ Add to playlist")
            self.b_add.clicked.connect(self.board_add_to_playlist)
            self.b_del = QtWidgets.QPushButton("Delete")
            self.b_del.clicked.connect(self.board_delete)
            self.b_refresh = QtWidgets.QPushButton("↻ Refresh")
            self.b_refresh.clicked.connect(self.ctl.refresh)
            for b in (self.b_play, self.b_add, self.b_del):
                row.addWidget(b)
            row.addStretch(1)
            row.addWidget(self.b_refresh)
            bl.addLayout(row)
            g.addWidget(box, 6, 0, 1, 4)
            g.setRowStretch(6, 1)
            return w

        def _bright(self):
            self.ctl.light(state="ON", brightness=round(self.bright.value() * 255 / 100))

        def _board_items(self):
            s = self.ctl.state
            return [("custom:" + c, c) for c in s.customs], [("playlist:" + p, p) for p in s.playlists]

        def _fill_board_list(self):
            sel = self.selected_board_mode()
            self.blist.clear()
            customs, pls = self._board_items()
            playing = self.ctl.state.now_playing
            active = self.ctl.state.mode

            def header(t):
                it = QtWidgets.QListWidgetItem(t)
                it.setFlags(Qt.NoItemFlags)
                f = it.font()
                f.setBold(True)
                it.setFont(f)
                self.blist.addItem(it)

            def add(mode, label):
                hot = mode in (playing, active)
                it = QtWidgets.QListWidgetItem(("▶ " if hot else "   ") + label + ("   (playing)" if hot else ""))
                it.setData(Qt.UserRole, mode)
                if hot:
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                    it.setBackground(QtGui.QColor(60, 140, 80, 90))
                self.blist.addItem(it)
                if mode == sel:
                    it.setSelected(True)
                    self.blist.setCurrentItem(it)
            header("Displays (%d)" % len(customs))
            for m, l in customs:
                add(m, l)
            header("Playlists (%d)" % len(pls))
            for m, l in pls:
                add(m, l)

        def selected_board_mode(self):
            it = self.blist.currentItem()
            return it.data(Qt.UserRole) if it and it.isSelected() else None

        def select_board_mode(self, mode):
            for i in range(self.blist.count()):
                it = self.blist.item(i)
                if it.data(Qt.UserRole) == mode:
                    self.blist.setCurrentItem(it)
                    it.setSelected(True)
                    return True
            return False

        def playing_row_text(self):
            for i in range(self.blist.count()):
                it = self.blist.item(i)
                if it.text().startswith("▶"):
                    return it.data(Qt.UserRole)
            return None

        def board_play(self):
            m = self.selected_board_mode()
            if m:
                self.ctl.play(m)

        def board_add_to_playlist(self):
            m = self.selected_board_mode()
            if not m:
                return
            if m.startswith("playlist:"):
                self._log("a playlist cannot contain another playlist", False)
                return
            self.add_item(m)
            self._log("added %s to playlist %s · open Playlists to name it, Save, then Send to board" % (
                m, self.pl["name"] or "(new, not named yet)"), None)

        def board_delete(self):
            m = self.selected_board_mode()
            if not m:
                return
            kind, name = m.split(":", 1)
            if not self.confirm("Delete %s “%s” from the board?" % ("display" if kind == "custom" else "playlist", name)):
                return
            if kind == "custom":
                self.ctl.delete_custom(name)
            else:
                self.ctl.delete_playlist_on_board(name)

        def _board_menu(self, pos):
            it = self.blist.itemAt(pos)
            if not it or not it.data(Qt.UserRole):
                return
            self.blist.setCurrentItem(it)
            m = QtWidgets.QMenu(self)
            m.addAction("Play", self.board_play)
            a = m.addAction("Add to playlist", self.board_add_to_playlist)
            a.setEnabled(it.data(Qt.UserRole).startswith("custom:"))
            m.addAction("Delete from board", self.board_delete)
            m.addSeparator()
            m.addAction("Refresh", self.ctl.refresh)
            m.exec(self.blist.mapToGlobal(pos))

        # ---------- library tab
        def _library_tab(self):
            w = QtWidgets.QWidget()
            h = QtWidgets.QHBoxLayout(w)
            left = QtWidgets.QVBoxLayout()
            self.lib_label = QtWidgets.QLabel("")
            self.lib_label.setWordWrap(True)
            self.lib_filter = QtWidgets.QLineEdit()
            self.lib_filter.setPlaceholderText("filter")
            self.lib_filter.textChanged.connect(self._fill_library)
            self.llist = QtWidgets.QListWidget()
            self.llist.currentItemChanged.connect(self._lib_selected)
            self.llist.itemDoubleClicked.connect(lambda *_: self.open_selected_in_editor())
            row = QtWidgets.QHBoxLayout()
            for t, f in (("Reload", self.reload_library), ("Import…", self.import_files), ("Open folder", self.open_lib_folder)):
                b = QtWidgets.QPushButton(t)
                b.clicked.connect(f)
                row.addWidget(b)
            left.addWidget(self.lib_label)
            left.addWidget(self.lib_filter)
            left.addWidget(self.llist, 1)
            left.addLayout(row)
            right = QtWidgets.QVBoxLayout()
            self.preview = PixelView()
            self.lib_info = QtWidgets.QLabel("")
            self.fit = QtWidgets.QComboBox()
            self.fit.addItems(["fit", "fill", "stretch"])
            self.fit.setToolTip("How a display of another size is scaled to the board: fit = letterbox, fill = crop")
            self.b_send = QtWidgets.QPushButton("⬆ Upload to board")
            self.b_send.setToolTip("Uploads this display to the board. Playlists only use displays that are on the board.")
            self.b_send.clicked.connect(self.send_selected)
            self.lib_play_after = QtWidgets.QCheckBox("Play it after the upload")
            b_open = QtWidgets.QPushButton("Open in editor")
            b_open.clicked.connect(self.open_selected_in_editor)
            fr = QtWidgets.QHBoxLayout()
            fr.addWidget(QtWidgets.QLabel("Scale"))
            fr.addWidget(self.fit)
            fr.addStretch(1)
            right.addWidget(self.preview, 1)
            right.addWidget(self.lib_info)
            right.addLayout(fr)
            right.addWidget(b_open)
            right.addWidget(self.b_send)
            right.addWidget(self.lib_play_after)
            h.addLayout(left, 1)
            h.addLayout(right, 1)
            return w

        def reload_library(self):
            lib = self.ctl.lib()
            self.lib_label.setText("Library: " + lib)
            self.llist.clear()
            self.llist.addItem("loading…")

            def work():
                try:
                    core.seed_library(lib, BUNDLED_ANIMATIONS)
                except OSError as e:
                    _log("could not seed library:", e)
                files = core.list_library(lib)
                self._lib_ready.emit(files)
            threading.Thread(target=work, name="library", daemon=True).start()

        def _library_loaded(self, files):
            self.library_files = list(files)
            self._fill_library()

        def _fill_library(self):
            q = self.lib_filter.text().lower()
            self.llist.clear()
            for f in self.library_files:
                if q in f.lower():
                    self.llist.addItem(f)
            if not self.library_files:
                self.llist.addItem("(empty: use Import…, or set the library folder in Settings)")
                self.llist.item(0).setFlags(Qt.NoItemFlags)

        def selected_library_file(self):
            it = self.llist.currentItem()
            return it.text() if it and it.text() in self.library_files else None

        def select_library_file(self, rel):
            m = self.llist.findItems(rel, Qt.MatchExactly)
            if m:
                self.llist.setCurrentItem(m[0])
            return bool(m)

        def _lib_selected(self, *_):
            rel = self.selected_library_file()
            if not rel:
                self.cur_disp = None
                self.preview.set_display(None)
                self.lib_info.setText("")
                return
            try:
                d = core.load_display(os.path.join(self.ctl.lib(), rel))
            except core.DisplayError as e:
                self.cur_disp = None
                self.preview.set_display(None)
                self.lib_info.setText(str(e))
                return
            self.cur_disp = d
            self.preview.set_display(d)
            s = self.ctl.state
            tgt = " → %d×%d on the board" % (s.w, s.h) if s.w and (s.w, s.h) != (d.w, d.h) else ""
            self.lib_info.setText("%s · %d×%d · %d frame(s)%s" % (core.board_name(d.name), d.w, d.h, len(d.frames), tgt))

        def send_selected(self):
            if not self.cur_disp:
                self._log("select a display in the library first", False)
                return
            self.ctl.send_display(self.cur_disp, self.fit.currentText(), play=self.lib_play_after.isChecked())

        def open_selected_in_editor(self):
            rel = self.selected_library_file()
            if rel and self.cur_disp:
                self.editor_open(self.cur_disp, os.path.join(self.ctl.lib(), rel))

        def open_lib_folder(self):
            lib = self.ctl.lib()
            os.makedirs(lib, exist_ok=True)
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(lib))

        def import_files(self):
            files, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "Import", os.path.expanduser("~"),
                                                              "Images and displays (*.gif *.png *.jpg *.jpeg *.json)")
            self.import_paths(files)

        def import_paths(self, files):
            s = self.ctl.state
            w, h = (s.w, s.h) if s.w else (32, 32)
            n, errs, last = 0, [], None
            for f in files:
                try:
                    d = core.load_display(f) if f.lower().endswith(".json") else import_image(f, w, h)
                    dst = os.path.join(self.ctl.lib(), "Imported", core.board_name(d.name) + ".json")
                    core.save_display(d, dst)
                    n += 1
                    last = os.path.relpath(dst, self.ctl.lib())
                except Exception as e:
                    errs.append("%s: %s" % (os.path.basename(f), e))
            self._log("imported %d file(s) into Imported/" % n + (" · errors: " + "; ".join(errs) if errs else ""), not errs)
            self.reload_library()
            return last

        # ---------- editor tab (pixel editor, same tools as the Mac app)
        def _saved_canvas(self):
            try:
                return json.load(open(core.settings_path())).get("canvas", "auto")
            except (OSError, ValueError, AttributeError):
                return "auto"

        def _editor_tab(self):
            w = QtWidgets.QWidget()
            v = QtWidgets.QVBoxLayout(w)
            tb = QtWidgets.QHBoxLayout()
            self.tool_group = QtWidgets.QButtonGroup(self)
            self.tool_buttons = {}
            for t, label in (("pencil", "✏ Pencil"), ("eraser", "Erase"), ("fill", "Fill"), ("picker", "Pick colour"), ("text", "Text")):
                b = QtWidgets.QToolButton()
                b.setText(label)
                b.setCheckable(True)
                b.clicked.connect(lambda _=False, t=t: self.set_tool(t))
                self.tool_group.addButton(b)
                self.tool_buttons[t] = b
                tb.addWidget(b)
            self.tool_buttons["pencil"].setChecked(True)
            self.stamp_text = QtWidgets.QLineEdit("HI")
            self.stamp_text.setMaximumWidth(90)
            self.stamp_text.setToolTip("Text stamped by the Text tool (3×5 pixel font)")
            self.stamp_text.textChanged.connect(lambda t: setattr(self.canvas, "text", t))
            tb.addWidget(self.stamp_text)
            tb.addStretch(1)
            tb.addWidget(QtWidgets.QLabel("Canvas"))
            self.canvas_combo = QtWidgets.QComboBox()
            self.canvas_combo.addItem("Match the board", "auto")
            for k, (cw, ch) in core.BOARDS.items():
                self.canvas_combo.addItem("%s %d×%d" % (k.capitalize(), cw, ch), k)
            self.canvas_combo.activated.connect(self._canvas_chosen)
            tb.addWidget(self.canvas_combo)
            for t, f, key in (("Undo", self.editor_undo, QtGui.QKeySequence.Undo), ("New", self.editor_new, None),
                              ("Save", self.editor_save, QtGui.QKeySequence.Save), ("Save As…", self.editor_save_as, None)):
                b = QtWidgets.QPushButton(t)
                b.clicked.connect(f)
                if key is not None:
                    b.setShortcut(key)
                tb.addWidget(b)
            v.addLayout(tb)
            mid = QtWidgets.QHBoxLayout()
            self.canvas = Canvas(self.ed)
            self.canvas.changed.connect(self._editor_changed)
            self.canvas.picked.connect(self._colour_changed)
            mid.addWidget(self.canvas, 1)
            side = QtWidgets.QVBoxLayout()
            nr = QtWidgets.QHBoxLayout()
            nr.addWidget(QtWidgets.QLabel("Name"))
            self.ed_name = QtWidgets.QLineEdit(self.ed.name)
            self.ed_name.setValidator(QtGui.QRegularExpressionValidator(QtCore.QRegularExpression(r"[A-Za-z0-9 _-]{0,24}"), self))
            self.ed_name.textEdited.connect(self._ed_name_edited)
            nr.addWidget(self.ed_name)
            side.addLayout(nr)
            cr = QtWidgets.QHBoxLayout()
            self.colour_btn = QtWidgets.QPushButton("Colour…")
            self.colour_btn.clicked.connect(self.choose_colour)
            cr.addWidget(self.colour_btn)
            side.addLayout(cr)
            pal = QtWidgets.QGridLayout()
            for i, c in enumerate(core.PALETTE):
                pal.addWidget(self._swatch(c), i // 4, i % 4)
            side.addLayout(pal)
            side.addWidget(QtWidgets.QLabel("Recent"))
            self.recent_box = QtWidgets.QGridLayout()
            self.recent_box.setSpacing(4)
            self.recent_box.setAlignment(Qt.AlignLeft)
            side.addLayout(self.recent_box)
            side.addWidget(QtWidgets.QLabel("Frame tools"))
            r1 = QtWidgets.QHBoxLayout()
            for t, f in (("←", lambda: self._ed(lambda: self.ed.shift(-1, 0))), ("→", lambda: self._ed(lambda: self.ed.shift(1, 0))),
                         ("↑", lambda: self._ed(lambda: self.ed.shift(0, -1))), ("↓", lambda: self._ed(lambda: self.ed.shift(0, 1)))):
                b = QtWidgets.QPushButton(t)
                b.setMaximumWidth(34)
                b.clicked.connect(f)
                r1.addWidget(b)
            side.addLayout(r1)
            r2 = QtWidgets.QHBoxLayout()
            for t, f in (("Flip", lambda: self._ed(self.ed.flip_h)), ("Clear", lambda: self._ed(self.ed.clear_frame))):
                b = QtWidgets.QPushButton(t)
                b.clicked.connect(f)
                r2.addWidget(b)
            side.addLayout(r2)
            self.onion = QtWidgets.QCheckBox("Onion skin")
            self.onion.toggled.connect(lambda c: (setattr(self.canvas, "onion", c), self.canvas.update()))
            side.addWidget(self.onion)
            side.addSpacing(8)
            self.b_ed_up = QtWidgets.QPushButton("⬆ Upload to board")
            self.b_ed_up.setToolTip("Uploads this display to the board (it does not have to be saved first)")
            self.b_ed_up.clicked.connect(self.editor_upload)
            side.addWidget(self.b_ed_up)
            self.ed_play_after = QtWidgets.QCheckBox("Play it after the upload")
            self.ed_play_after.setChecked(True)
            side.addWidget(self.ed_play_after)
            self.live = QtWidgets.QCheckBox("Live (mirror the canvas)")
            self.live.setToolTip("Shows the canvas on the board while you draw. Nothing is stored on the board.")
            self.live.toggled.connect(self._live_toggled)
            side.addWidget(self.live)
            self.ed_hint = QtWidgets.QLabel("")
            self.ed_hint.setWordWrap(True)
            self.ed_hint.setStyleSheet("color: gray")
            side.addWidget(self.ed_hint)
            side.addStretch(1)
            sw = QtWidgets.QWidget()
            sw.setLayout(side)
            sw.setFixedWidth(210)
            mid.addWidget(sw)
            v.addLayout(mid, 1)
            fr = QtWidgets.QHBoxLayout()
            self.frame_label = QtWidgets.QLabel("")
            fr.addWidget(self.frame_label)
            for t, f in (("＋ Add", self.ed.add_frame), ("Duplicate", self.ed.dup_frame), ("Delete", self.ed.del_frame),
                         ("◀ Move", lambda: self.ed.move_frame(-1)), ("Move ▶", lambda: self.ed.move_frame(1))):
                b = QtWidgets.QPushButton(t)
                b.clicked.connect(lambda _=False, f=f: self._ed(f))
                fr.addWidget(b)
            self.b_anim = QtWidgets.QPushButton("▶ Play")
            self.b_anim.clicked.connect(self.toggle_anim)
            fr.addWidget(self.b_anim)
            fr.addWidget(QtWidgets.QLabel("ms"))
            self.dur = QtWidgets.QSpinBox()
            self.dur.setRange(core.DUR_MIN, core.DUR_MAX)
            self.dur.setSingleStep(50)
            self.dur.editingFinished.connect(lambda: self._ed(lambda: self.ed.set_duration(self.dur.value())))
            fr.addWidget(self.dur)
            b_all = QtWidgets.QPushButton("all")
            b_all.setToolTip("Use this duration for every frame")
            b_all.clicked.connect(lambda: self._ed(lambda: self.ed.set_duration(self.dur.value(), True)))
            fr.addWidget(b_all)
            self.frame_warn = QtWidgets.QLabel("")
            self.frame_warn.setStyleSheet("color: #d97a00")
            fr.addWidget(self.frame_warn)
            fr.addStretch(1)
            v.addLayout(fr)
            self.strip = QtWidgets.QListWidget()
            self.strip.setViewMode(QtWidgets.QListView.IconMode)
            self.strip.setFlow(QtWidgets.QListView.LeftToRight)
            self.strip.setWrapping(False)
            self.strip.setIconSize(QtCore.QSize(56, 56))
            self.strip.setFixedHeight(96)
            self.strip.setMovement(QtWidgets.QListView.Static)
            self.strip.currentRowChanged.connect(self._strip_selected)
            v.addWidget(self.strip)
            self.anim = QtCore.QTimer(self, singleShot=True, timeout=self._anim_step)
            self._live_timer = QtCore.QTimer(self, singleShot=True, interval=250, timeout=self._live_push)
            self._live_t = 0.0
            i = self.canvas_combo.findData(self._saved_canvas())
            self.canvas_combo.setCurrentIndex(max(0, i))
            self._editor_refresh()
            return w

        def _swatch(self, c):
            b = QtWidgets.QToolButton()
            b.setFixedSize(26, 26)
            b.setStyleSheet("background: #%06x; border: 1px solid #777" % c)
            b.setToolTip("#%06x" % c)
            b.clicked.connect(lambda: self.use_colour(c))
            return b

        def set_tool(self, t):
            self.canvas.tool = t
            self.tool_buttons[t].setChecked(True)

        def use_colour(self, c):
            self.ed.use_color(c)
            self._colour_changed()

        def choose_colour(self):
            c = QtWidgets.QColorDialog.getColor(_qc(self.ed.color), self, "Colour")
            if c.isValid():
                self.use_colour(c.rgb() & 0xFFFFFF)

        def _colour_changed(self):
            c = self.ed.color
            self.colour_btn.setStyleSheet("background: #%06x; color: %s" % (c, "black" if (c >> 8 & 255) > 140 else "white"))
            self.colour_btn.setText("Colour #%06x" % c)
            while self.recent_box.count():
                it = self.recent_box.takeAt(0)
                if it.widget():
                    it.widget().hide()
                    it.widget().deleteLater()
            for i, rc in enumerate(self.ed.recent):
                self.recent_box.addWidget(self._swatch(rc), i // 4, i % 4)

        def _ed(self, f):
            f()
            self._editor_changed()

        def editor_undo(self):
            if self.ed.undo():
                self._editor_changed()

        def _ed_name_edited(self, t):
            self.ed.name = t.strip() or "untitled"
            self.ed.dirty = True
            self._editor_refresh(strip=False)

        def _editor_changed(self):
            self._editor_refresh()
            self._live_changed()

        def _editor_refresh(self, strip=True):
            e = self.ed
            self.canvas.update()
            if strip:
                self.strip.blockSignals(True)
                self.strip.clear()
                for i, (px, d) in enumerate(e.frames):
                    it = QtWidgets.QListWidgetItem(frame_icon(px, e.w, e.h), "%d ms" % d)
                    self.strip.addItem(it)
                self.strip.setCurrentRow(e.cur)
                self.strip.blockSignals(False)
            self.frame_label.setText("Frame %d/%d" % (e.cur + 1, len(e.frames)))
            if not self.dur.hasFocus():
                self.dur.setValue(e.frames[e.cur][1])
            self.frame_warn.setText("the board plays the first %d frames" % core.MAX_FRAMES if len(e.frames) > core.MAX_FRAMES else "")
            if self.ed_name.text() != e.name and not self.ed_name.hasFocus():
                self.ed_name.setText(e.name)
            where = os.path.relpath(e.path, self.ctl.lib()) if e.path else "not saved yet"
            self.ed_hint.setText("%s%s · %d×%d · %s" % (core.board_name(e.name), " (unsaved changes)" if e.dirty else "",
                                                        e.w, e.h, where) + self._size_note())

        def _size_note(self):
            s = self.ctl.state
            if s.w and (s.w, s.h) != (self.ed.w, self.ed.h):
                return " · the board is %d×%d: uploading scales it, live is off" % (s.w, s.h)
            return ""

        def _strip_selected(self, r):
            if 0 <= r < len(self.ed.frames) and r != self.ed.cur:
                self.ed.cur = r
                self._editor_refresh(strip=False)
                self._live_changed()

        def toggle_anim(self):
            if self.anim.isActive():
                self.anim.stop()
                self.b_anim.setText("▶ Play")
            else:
                self.b_anim.setText("■ Stop")
                self.anim.start(self.ed.frames[self.ed.cur][1])

        def _anim_step(self):
            self.ed.cur = self.ed.next_index()
            self.strip.blockSignals(True)
            self.strip.setCurrentRow(self.ed.cur)
            self.strip.blockSignals(False)
            self._editor_refresh(strip=False)
            self._live_changed()
            self.anim.start(self.ed.frames[self.ed.cur][1])

        def _canvas_chosen(self, i):
            k = self.canvas_combo.itemData(i)
            try:
                st = json.load(open(core.settings_path()))
            except (OSError, ValueError):
                st = {}
            if isinstance(st, dict):
                st["canvas"] = k
                try:
                    core.write_settings(st)
                except (OSError, AttributeError):
                    pass
            self._apply_canvas()

        def _apply_canvas(self):
            k = self.canvas_combo.currentData()
            s = self.ctl.state
            if k == "auto":
                if not s.w:
                    return
                w, h = s.w, s.h
            else:
                w, h = core.BOARDS[k]
            if (w, h) != (self.ed.w, self.ed.h):
                self.ed.set_canvas(w, h, self.fit.currentText())
                self._editor_changed()
            self._live_enable()

        def _editor_board_changed(self):
            if self.canvas_combo.currentData() == "auto":
                self._apply_canvas()
            self._live_enable()
            self._editor_refresh(strip=False)

        def _live_enable(self):
            self.live.setEnabled(self.ctl.connected and self.ctl.state.v2 and not self._size_note())
            if not self.live.isEnabled() and self.live.isChecked():
                self.live.setChecked(False)

        def editor_new(self):
            if self.ed.dirty and not self.confirm("Discard the unsaved changes to %s?" % self.ed.name):
                return
            self.ed.new()
            self._editor_changed()

        def editor_open(self, disp, path=None):
            if self.ed.dirty and not self.confirm("Discard the unsaved changes to %s?" % self.ed.name):
                return False
            native = (disp.w, disp.h)
            self.ed.load(disp, path, self.fit.currentText())
            self._editor_changed()
            self.tabs.setCurrentIndex(1)
            self._log("opened %s (%d frames%s)" % (os.path.relpath(path, self.ctl.lib()) if path else disp.name, len(self.ed.frames),
                      ", %d×%d → %d×%d" % (native + (self.ed.w, self.ed.h)) if native != (self.ed.w, self.ed.h) else ""), None)
            return True

        def editor_save(self, path=None):
            e = self.ed
            path = path or e.path or os.path.join(self.ctl.lib(), core.board_name(e.name) + ".json")
            try:
                e.save(path)
            except (OSError, ValueError) as ex:
                self._log("save failed: %s" % ex, False)
                return None
            self._log("saved %s" % os.path.relpath(path, self.ctl.lib()), True)
            self._editor_refresh(strip=False)
            self.reload_library()
            return path

        def editor_save_as(self):
            start = os.path.join(self.ctl.lib(), core.board_name(self.ed.name) + ".json")
            p, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save display", start, "Unicorn display (*.json)")
            if p:
                if not p.endswith(".json"):
                    p += ".json"
                return self.editor_save(p)
            return None

        def editor_upload(self):
            if self.live.isChecked():
                self.live.setChecked(False)
            d = self.ed.to_display()
            self.ctl.send_display(d, self.fit.currentText(), play=self.ed_play_after.isChecked())

        # live mirror: at most 4 frames a second, the last change always goes out
        def _live_toggled(self, on):
            if on:
                self._live_t = 0.0
                self._live_push()
            else:
                self._live_timer.stop()
                self.ctl.live_off()
                QtCore.QTimer.singleShot(800, self, self.ctl.refresh)

        def _live_changed(self):
            if not self.live.isChecked():
                return
            gap = time.time() - self._live_t
            if gap >= 0.25:
                self._live_push()
            elif not self._live_timer.isActive():
                self._live_timer.start(int((0.25 - gap) * 1000) + 1)

        def _live_push(self):
            if self.live.isChecked() and not self._size_note():
                self._live_t = time.time()
                if not self.ctl.live_frame(self.ed.px):
                    self.live.setChecked(False)

        # ---------- playlists tab
        # Flow: add displays from 'On the board' (+ built-in modes) -> name it -> Save -> Send to board -> Play.
        # Send transfers ONLY the playlist; displays get to the board through Upload (Library / Editor).
        def _playlist_tab(self):
            w = QtWidgets.QWidget()
            v = QtWidgets.QVBoxLayout(w)
            steps = QtWidgets.QLabel("① Add displays from the Board tab (“On the board” → ＋ Add to playlist) and built-in modes below"
                                     " · ② Name it · ③ Save · ④ Send to board · ⑤ Play")
            steps.setWordWrap(True)
            v.addWidget(steps)
            top = QtWidgets.QHBoxLayout()
            top.addWidget(QtWidgets.QLabel("Playlist"))
            self.pl_combo = QtWidgets.QComboBox()
            self.pl_combo.setMinimumWidth(320)
            self.pl_combo.activated.connect(lambda i: self.pick_playlist(self.pl_combo.itemData(i)))
            top.addWidget(self.pl_combo)
            self.b_pl_new = QtWidgets.QPushButton("New")
            self.b_pl_new.clicked.connect(self.new_playlist)
            self.b_pl_import = QtWidgets.QPushButton("Import from board")
            self.b_pl_import.clicked.connect(self.import_board_playlist)
            self.b_pl_del = QtWidgets.QPushButton("Delete…")
            self.b_pl_del.clicked.connect(self.delete_playlist_ui)
            for b in (self.b_pl_new, self.b_pl_import, self.b_pl_del):
                top.addWidget(b)
            top.addStretch(1)
            v.addLayout(top)
            nr = QtWidgets.QHBoxLayout()
            nr.addWidget(QtWidgets.QLabel("Name"))
            self.pl_name = QtWidgets.QLineEdit()
            self.pl_name.setPlaceholderText("playlist name: a-z 0-9 _ - (up to 24)")
            self.pl_name.setMaxLength(24)
            self.pl_name.setValidator(QtGui.QRegularExpressionValidator(QtCore.QRegularExpression(r"[a-z0-9_-]{0,24}"), self))
            self.pl_name.textEdited.connect(self._pl_name_edited)
            nr.addWidget(self.pl_name, 1)
            self.b_pl_save = QtWidgets.QPushButton("Save")
            self.b_pl_save.clicked.connect(self.save_playlist)
            nr.addWidget(self.b_pl_save)
            v.addLayout(nr)
            self.pl_model = QtGui.QStandardItemModel(0, 4, self)
            self.pl_model.setHorizontalHeaderLabels(["Item", "Seconds", "Speed", "On the board?"])
            self.pl_view = PlaylistView()
            self.pl_view.setModel(self.pl_model)
            self.pl_view.setRootIsDecorated(False)
            self.pl_view.setItemsExpandable(False)
            self.pl_view.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
            self.pl_view.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
            self.pl_view.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
            self.pl_view.setDefaultDropAction(Qt.MoveAction)
            self.pl_view.setDragDropOverwriteMode(False)
            self.pl_view.setDropIndicatorShown(True)
            self.pl_view.setItemDelegateForColumn(1, SecondsDelegate(self.pl_view))
            self.pl_view.setItemDelegateForColumn(2, SpeedDelegate(self.pl_view))
            self.pl_view.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked | QtWidgets.QAbstractItemView.SelectedClicked
                                         | QtWidgets.QAbstractItemView.EditKeyPressed)
            self.pl_view.header().setStretchLastSection(False)
            self.pl_view.header().setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
            self.pl_view.setColumnWidth(2, 130)
            self.pl_view.delete_pressed.connect(self.remove_selected_item)
            self.pl_model.rowsMoved.connect(self._pl_from_model)
            self.pl_model.rowsRemoved.connect(self._pl_from_model)
            self.pl_model.itemChanged.connect(self._pl_from_model)
            v.addWidget(self.pl_view, 1)
            row = QtWidgets.QHBoxLayout()
            for t, f in (("↑", lambda: self.move_selected(-1)), ("↓", lambda: self.move_selected(1)), ("− Remove", self.remove_selected_item)):
                b = QtWidgets.QPushButton(t)
                b.clicked.connect(f)
                row.addWidget(b)
            row.addSpacing(20)
            row.addWidget(QtWidgets.QLabel("Built-in mode"))
            self.add_mode_combo = QtWidgets.QComboBox()
            self.add_mode_combo.setMinimumWidth(140)
            self.add_mode_combo.addItems(list(core.BUILTIN_MODES))
            b_am = QtWidgets.QPushButton("＋ Add mode")
            b_am.clicked.connect(lambda: self.add_mode_combo.currentText() and self.add_item(self.add_mode_combo.currentText()))
            row.addWidget(self.add_mode_combo)
            row.addWidget(b_am)
            row.addStretch(1)
            v.addLayout(row)
            hint = QtWidgets.QLabel("Drag rows or use ↑ ↓ to reorder · double-click Seconds (2–3600) or Speed (1–20, "
                                    "Normal 10 = the display's own timing) to change them · "
                                    "displays marked ⚠ are not on the board: upload them from the Library or Editor tab, "
                                    "otherwise the board skips them.")
            hint.setWordWrap(True)
            hint.setStyleSheet("color: gray")
            v.addWidget(hint)
            self.pl_status = QtWidgets.QLabel("")
            self.pl_status.setWordWrap(True)
            v.addWidget(self.pl_status)
            row2 = QtWidgets.QHBoxLayout()
            row2.addStretch(1)
            self.b_pl_send = QtWidgets.QPushButton("④ Send to board")
            self.b_pl_send.setToolTip("Sends only the playlist (names and seconds). Displays are never uploaded from here.")
            self.b_pl_send.clicked.connect(self.send_playlist)
            self.b_pl_play = QtWidgets.QPushButton("⑤ ▶ Play on board")
            self.b_pl_play.clicked.connect(self.play_playlist)
            row2.addWidget(self.b_pl_send)
            row2.addWidget(self.b_pl_play)
            v.addLayout(row2)
            self._busy_now = False
            return w

        def store(self):
            return core.Playlists(self.ctl.lib())

        # picker: local playlists (on this computer, maybe also on the board) + board-only ones (importable)
        def _fill_pl_combo(self):
            local = self.store().list()
            board = list(self.ctl.state.playlists)
            want = self.pl_loaded or ""
            self.pl_combo.blockSignals(True)
            self.pl_combo.clear()
            if not self.pl_loaded:
                self.pl_combo.addItem("(new playlist%s)" % (": " + self.pl["name"] if self.pl["name"] else ""), "")
            for n in local:
                self.pl_combo.addItem("%s — this computer%s" % (n, " + board" if n in board else ""), n)
            for n in board:
                if n not in local:
                    self.pl_combo.addItem("%s — on the board only" % n, "board:" + n)
            i = self.pl_combo.findData(want)
            self.pl_combo.setCurrentIndex(max(0, i))
            self.pl_combo.blockSignals(False)

        def refresh_playlists(self):
            names = self.store().list()
            if self.pl_loaded and self.pl_loaded not in names:
                self.pl_loaded = None
            if not self.pl_loaded and not self.pl_dirty and names:
                self._load_local(names[0])
            else:
                self._pl_to_model()
            self._fill_pl_combo()
            self._pl_buttons()

        def _discard_ok(self):
            return not self.pl_dirty or self.confirm("Discard the unsaved changes to playlist %s?" % (self.pl["name"] or "(new)"))

        def _load_local(self, name):
            p = self.store().load(name)
            if not p:
                return False
            self.pl, self.pl_loaded, self.pl_dirty = p, p["name"], False
            self._pl_to_model()
            return True

        def pick_playlist(self, data):
            if data == (self.pl_loaded or ""):
                return
            if not self._discard_ok():
                self._fill_pl_combo()
                return
            if data.startswith("board:"):
                self.import_board_playlist(data[6:])
            elif data:
                self._load_local(data)
            self._fill_pl_combo()
            self._pl_buttons()

        def select_playlist(self, name):
            if self._load_local(name):
                self._fill_pl_combo()
                self._pl_buttons()

        def import_board_playlist(self, name=None):
            if not name:
                d = self.pl_combo.currentData() or ""
                name = d[6:] if d.startswith("board:") else None
            if not name:
                return False
            if name in self.store().list():
                self._load_local(name)
                self._log("playlist %s already has a copy on this computer" % name, None)
            elif self.ctl.state.has_get:
                self._log("asking the board for playlist %s…" % name, None)
                self.b_pl_import.setEnabled(False)
                self.ctl.fetch_playlist(name)
                return True
            else:
                self.pl = {"name": name, "items": []}
                self.store().save(self.pl)
                self.pl_loaded, self.pl_dirty = name, False
                self._pl_to_model()
                self._log("imported %s from the board. This firmware (%s) reports a playlist's name but not its items "
                          "(us-2.1 can), so the copy starts empty: add the items again, Save, then Send."
                          % (name, self.ctl.state.version), "warn")
            self._fill_pl_combo()
            self._pl_buttons()
            return True

        def _playlist_items(self, name, items, err):
            if items is None:
                self._log("✗ could not import %s from the board: %s" % (name, err), False)
            elif not items:
                self._log("✗ the board's playlist %s has no items" % name, False)
            elif name in self.store().list():
                self._log("playlist %s already has a copy on this computer; not overwritten" % name, None)
            else:
                self.pl = {"name": name, "items": items}
                self.store().save(self.pl)
                self.pl_loaded, self.pl_dirty = name, False
                self._pl_to_model()
                self._log("✓ imported %s from the board: %d items (%s)" % (
                    name, len(items), ", ".join(i["mode"] for i in items[:6]) + (" …" if len(items) > 6 else "")), True)
            self._fill_pl_combo()
            self._pl_buttons()

        def new_playlist(self):
            if not self._discard_ok():
                return
            self.pl, self.pl_loaded, self.pl_dirty = {"name": "", "items": []}, None, False
            self._pl_to_model()
            self._fill_pl_combo()
            self._pl_buttons()
            self.pl_name.setFocus()

        def _pl_name_edited(self, t):
            self.pl["name"] = t
            self._mark_dirty()

        def _mark_dirty(self):
            self.pl_dirty = True
            self._pl_buttons()

        def save_playlist(self):
            n = self.pl_name.text().strip()
            if not n or core.board_name(n) != n:
                self._log("give the playlist a name first (a-z 0-9 _ -, up to 24)", False)
                self.pl_name.setFocus()
                return False
            if n != self.pl_loaded and n in self.store().list() and not self.confirm("Replace the playlist %s on this computer?" % n):
                return False
            self.pl["name"] = n
            try:
                self.store().save(self.pl)
            except OSError as e:
                self._log("playlist save failed: %s" % e, False)
                return False
            old = self.pl_loaded
            if old and old != n:
                self.store().delete(old)                 # renamed: the board copy of the old name is left alone
            self.pl_loaded, self.pl_dirty = n, False
            self._fill_pl_combo()
            self._pl_buttons()
            extra = " (the board still has the old name %s until you delete it there)" % old if old and old != n and old in self.ctl.state.playlists else ""
            self._log("saved playlist %s on this computer · next: ④ Send to board%s" % (n, extra), True)
            return True

        def delete_playlist_ui(self):
            n = self.pl_loaded
            if not n:
                self.new_playlist()
                return
            on_board = n in self.ctl.state.playlists
            if self.confirm("Delete playlist “%s” from this computer%s?" % (n, " and the board" if on_board else "")):
                self.delete_playlist()

        def delete_playlist(self):
            old = self.pl_loaded
            if old:
                self.store().delete(old)
                if old in self.ctl.state.playlists:
                    self.ctl.delete_playlist_on_board(old)
            self.pl, self.pl_loaded, self.pl_dirty = {"name": "", "items": []}, None, False
            self.refresh_playlists()

        def _pl_buttons(self):
            if not hasattr(self, "b_pl_send"):
                return
            n, s = self.pl_loaded, self.ctl.state
            on_board = bool(n) and n in s.playlists
            saved = bool(n) and not self.pl_dirty
            busy = getattr(self, "_busy_now", False)
            self.b_pl_save.setEnabled(self.pl_dirty or not n)
            self.b_pl_send.setEnabled(saved and bool(self.pl["items"]) and self.ctl.connected and not busy)
            self.b_pl_play.setEnabled(on_board and not self.pl_dirty and self.ctl.connected and not busy)
            d = self.pl_combo.currentData() or ""
            self.b_pl_import.setEnabled(d.startswith("board:"))
            if self.pl_name.text() != self.pl["name"]:
                self.pl_name.setText(self.pl["name"])
            missing = core.missing_on_board(self.pl, s.customs) if s.at else []
            parts = []
            if not n:
                parts.append("not saved yet: type a name and press Save")
            elif self.pl_dirty:
                parts.append("unsaved changes: press Save")
            else:
                parts.append("saved on this computer")
            if s.at:
                parts.append("on the board: yes" if on_board else "on the board: no (press Send to board)")
            if missing:
                parts.append("⚠ not on the board: " + ", ".join(missing))
            self.pl_status.setText(" · ".join(parts))
            self.pl_status.setStyleSheet("color: %s" % ("#d97a00" if missing or self.pl_dirty or not n else "palette(text)"))

        def _pl_board_changed(self):
            self._flag_rows()
            d = self.pl_combo.currentData()
            self._fill_pl_combo()
            if d is not None and self.pl_combo.currentData() != d:
                self.pl_combo.setCurrentIndex(max(0, self.pl_combo.findData(d)))
            self._pl_buttons()

        def _flag(self, mode):
            s = self.ctl.state
            if not mode.startswith("custom:"):
                return "built-in", None
            if not s.at:
                return "?", None
            return ("✓ yes", None) if mode[7:] in s.customs else ("⚠ not on the board", QtGui.QColor(217, 122, 0))

        def _row(self, it):
            a = QtGui.QStandardItem(it["mode"])
            a.setEditable(False)
            b = QtGui.QStandardItem()
            b.setData(core.clamp_seconds(it["seconds"]), Qt.EditRole)
            sp = QtGui.QStandardItem()
            sp.setData(core.clamp_speed(it.get("speed")) or 0, Qt.EditRole)
            sp.setToolTip("Speed while this item plays: 1 slowest … 10 Normal … 20 fastest")
            t, col = self._flag(it["mode"])
            c = QtGui.QStandardItem(t)
            c.setEditable(False)
            if col:
                c.setForeground(col)
                a.setForeground(col)
            for x in (a, b, sp, c):
                x.setFlags((x.flags() | Qt.ItemIsDragEnabled) & ~Qt.ItemIsDropEnabled)
            return [a, b, sp, c]

        def _flag_rows(self):
            self._loading_pl = True
            for r in range(self.pl_model.rowCount()):
                a, c = self.pl_model.item(r, 0), self.pl_model.item(r, 3)
                if a is None or c is None:
                    continue
                t, col = self._flag(a.text())
                if c.text() != t:
                    c.setText(t)
                    br = QtGui.QBrush(col) if col else QtGui.QBrush()
                    c.setForeground(br)
                    a.setForeground(br)
            self._loading_pl = False

        def flagged_rows(self):
            return [self.pl_model.item(r, 0).text() for r in range(self.pl_model.rowCount())
                    if self.pl_model.item(r, 3) and self.pl_model.item(r, 3).text().startswith("⚠")]

        def _pl_to_model(self):
            self._loading_pl = True
            self.pl_model.removeRows(0, self.pl_model.rowCount())
            for it in self.pl["items"]:
                self.pl_model.appendRow(self._row(it))
            self._loading_pl = False
            self._pl_buttons()

        def _pl_from_model(self, *_):
            if self._loading_pl:
                return
            QtCore.QTimer.singleShot(0, self._sync_pl)

        def _sync_pl(self):
            items = []
            for r in range(self.pl_model.rowCount()):
                a, b, sp = self.pl_model.item(r, 0), self.pl_model.item(r, 1), self.pl_model.item(r, 2)
                if a is None or not a.text():
                    continue
                v = sp.data(Qt.EditRole) if sp else None
                items.append(core._item(a.text(), b.data(Qt.EditRole) if b else 10, v or None))
            if items != self.pl["items"]:
                self.pl["items"] = items
                self._mark_dirty()

        def add_item(self, mode):
            if mode.startswith("playlist:"):
                self._log("a playlist cannot contain another playlist", False)
                return False
            if len(self.pl["items"]) >= 32:
                self._log("a playlist holds at most 32 items", False)
                return False
            self.pl["items"].append({"mode": mode, "seconds": 10, "speed": core.SPEED_NORMAL})
            self._pl_to_model()
            self._mark_dirty()
            return True

        def selected_row(self):
            idx = self.pl_view.selectionModel().selectedRows()
            return idx[0].row() if idx else -1

        def select_row(self, r):
            self.pl_view.selectionModel().select(self.pl_model.index(r, 0),
                                                 QtCore.QItemSelectionModel.ClearAndSelect | QtCore.QItemSelectionModel.Rows)
            self.pl_view.setCurrentIndex(self.pl_model.index(r, 0))

        def remove_selected_item(self):
            r = self.selected_row()
            if r < 0:
                return
            del self.pl["items"][r]
            self._pl_to_model()
            self._mark_dirty()
            if self.pl["items"]:
                self.select_row(min(r, len(self.pl["items"]) - 1))

        def move_selected(self, d):
            r = self.selected_row()
            n = r + d
            if r < 0 or not (0 <= n < len(self.pl["items"])):
                return
            it = self.pl["items"]
            it[r], it[n] = it[n], it[r]
            self._pl_to_model()
            self._mark_dirty()
            self.select_row(n)

        def set_speed(self, row, speed):
            self.pl_model.item(row, 2).setData(speed, Qt.EditRole)
            self._sync_pl()

        def set_seconds(self, row, secs):
            self.pl_model.item(row, 1).setData(secs, Qt.EditRole)
            self._sync_pl()

        def send_playlist(self, *_):
            self._sync_pl()
            if self.pl_dirty or not self.pl_loaded:
                self._log("press Save first: Send transfers the saved playlist", False)
                return False
            return self.ctl.send_playlist({"name": self.pl_loaded, "items": list(self.pl["items"])})

        def play_playlist(self, *_):
            if self.pl_loaded:
                return self.ctl.play_playlist(self.pl_loaded)
            return False

        # ---------- signals
        def _conn(self, ok, msg):
            self.conn.setText(("● " if ok else "○ ") + msg)
            self.conn.setStyleSheet("color: %s" % ("#2a9d4b" if ok else "#c0392b"))

        def _state(self):
            s = self.ctl.state
            st = s.info() or ("no answer from %s yet" % self.ctl.base if self.ctl.base else "no board selected")
            if s.speed_now is not None and s.speed_now != s.speed:
                st += " · playlist item speed %d" % s.speed_now
            if s.status == "offline":
                st += " · board OFFLINE"
            self.info.setText(st)
            for wdg, val in ((self.on, s.on),):
                wdg.blockSignals(True)
                wdg.setChecked(val)
                wdg.blockSignals(False)
            if not self.bright.isSliderDown() and not self._bright_debounce.isActive():
                self.bright.blockSignals(True)
                self.bright.setValue(s.brightness)
                self.bright.blockSignals(False)
            if not self.speed.isSliderDown():
                self.speed.setValue(s.speed)
            modes = s.all_modes()
            if [self.mode.itemText(i) for i in range(self.mode.count())] != modes:
                self.mode.clear()
                self.mode.addItems(modes)
            self.mode.setCurrentText(s.mode)
            if [self.rot.itemText(i) for i in range(self.rot.count())] != s.rotations:
                self.rot.clear()
                self.rot.addItems(s.rotations)
            self.rot.setCurrentText(s.rotation)
            if not self.text.hasFocus():
                self.text.setText(s.text)
            adds = list(s.builtins)
            if [self.add_mode_combo.itemText(i) for i in range(self.add_mode_combo.count())] != adds:
                self.add_mode_combo.clear()
                self.add_mode_combo.addItems(adds)
            self._fill_board_list()
            self._lib_selected()
            self._pl_board_changed()
            self._editor_board_changed()

        def _seen(self):
            cur = self.ctl.base
            self.boards.clear()
            for b, (kind, w, h, ver) in sorted(self.ctl.seen.items()):
                self.boards.addItem("%s · %s %s×%s" % (b, kind, w, h), b)
            i = self.boards.findData(cur)
            if i >= 0:
                self.boards.setCurrentIndex(i)

        def _log(self, msg, ok=None):
            self.status.setText(msg)
            self.status.setStyleSheet("color: %s" % {True: "#2a9d4b", False: "#c0392b", "warn": "#d97a00"}.get(ok, "palette(text)"))

        def _busy(self, b):
            self._busy_now = b
            self.b_send.setEnabled(not b)
            self.b_ed_up.setEnabled(not b)
            self._pl_buttons()

        def _confirm(self, q):
            return QtWidgets.QMessageBox.question(self, "Unicorn Studio", q) == QtWidgets.QMessageBox.Yes

        def open_settings(self):
            d = SettingsDialog(self.ctl, self)
            if d.exec() != QtWidgets.QDialog.Accepted:
                return
            c = d.values()
            stored = c.save(remember_password=d.remember.isChecked())
            self.ctl.cfg = c
            self.ctl.state.reset()
            self.ctl.connect_broker()
            self.reload_library()
            self.refresh_playlists()
            if d.remember.isChecked() and not stored:
                self._log("password could not be stored in the keyring; it is kept for this session only", False)

        def closeEvent(self, e):
            self.ctl.stop()
            super().closeEvent(e)

    def import_image(path, w, h):
        """GIF/PNG/JPEG -> w x h frames, aspect-fit on black (same as the Mac app)."""
        if os.path.getsize(path) > core.MAX_FILE_BYTES:
            raise core.DisplayError("file too large")
        r = QtGui.QImageReader(path)
        sz = r.size()
        if sz.width() * sz.height() > 4096 * 4096:
            raise core.DisplayError("image too large (%dx%d)" % (sz.width(), sz.height()))
        frames = []
        while len(frames) < core.MAX_FRAMES_READ:
            img = r.read()
            if img.isNull():
                break
            delay = r.nextImageDelay() if r.supportsAnimation() else 100
            dst = QtGui.QImage(w, h, QtGui.QImage.Format_RGB32)
            dst.fill(QtGui.QColor(0, 0, 0))
            p = QtGui.QPainter(dst)
            p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, img.width() > w or img.height() > h)
            s = min(w / img.width(), h / img.height())
            dw, dh = img.width() * s, img.height() * s
            p.drawImage(QtCore.QRectF((w - dw) / 2, (h - dh) / 2, dw, dh), img)
            p.end()
            px = []
            for y in range(h):
                for x in range(w):
                    c = dst.pixel(x, y) & 0xFFFFFF
                    px.append(0 if ((c >> 16) & 255) + ((c >> 8) & 255) + (c & 255) < 12 else c)
            frames.append((px, max(20, min(60000, delay if delay > 0 else 100))))
            if not r.supportsAnimation():
                break
        if not frames:
            raise core.DisplayError("cannot read image: " + r.errorString())
        if len(frames) > core.MAX_FRAMES:          # keep total timing, drop frames evenly
            step = -(-len(frames) // core.MAX_FRAMES)
            frames = [(frames[i][0], min(60000, sum(d for _, d in frames[i:i + step]))) for i in range(0, len(frames), step)]
        name = os.path.splitext(os.path.basename(path))[0]
        return core.Display(name, w, h, frames, "imported from " + os.path.basename(path))


# ------------------------------------------------------------------ desktop integration & CLI entry
def install_desktop(prefix=None):
    """Install the .desktop launcher and icon for the current user (XDG paths). No root needed."""
    share = prefix or os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    apps = os.path.join(share, "applications")
    icons = os.path.join(share, "icons", "hicolor", "scalable", "apps")
    os.makedirs(apps, exist_ok=True)
    os.makedirs(icons, exist_ok=True)
    exe = shutil.which("unicorn-studio")
    exe = ('"%s"' % exe if " " in exe else exe) if exe else ('"%s" -m unicorn_studio' % sys.executable)
    with open(os.path.join(DATA, "unicorn-studio.desktop"), encoding="utf-8") as f:
        desk = f.read().replace("Exec=unicorn-studio", "Exec=" + exe)
    with open(os.path.join(apps, "unicorn-studio.desktop"), "w", encoding="utf-8") as f:
        f.write(desk)
    shutil.copy2(os.path.join(DATA, "unicorn-studio.svg"), os.path.join(icons, "unicorn-studio.svg"))
    return os.path.join(apps, "unicorn-studio.desktop")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="unicorn-studio", description="Unicorn Studio for Linux")
    ap.add_argument("--version", action="version", version="Unicorn Studio %s" % VERSION)
    ap.add_argument("--install-desktop", action="store_true", help="add the app to your desktop menu (~/.local/share)")
    ap.add_argument("--check-config", action="store_true", help="show where settings come from (password is never shown)")
    a, qt_args = ap.parse_known_args(argv)
    if a.install_desktop:
        print("installed", install_desktop())
        return 0
    if a.check_config:
        c = core.ConnConfig.load()
        print("config files :", ", ".join(core.config_candidates()))
        print("settings     :", core.settings_path())
        print("broker       :", (c.host or "-") + ":" + str(c.port), "TLS" if c.tls else "")
        print("user         :", c.user or "-")
        print("password     :", "set" if c.password else "not set")
        print("board        :", c.base or "(first one found)")
        print("library      :", core.library_dir(c.library))
        print("keyring      :", "available" if core.keyring_available() else "not installed (optional)")
        return 0
    if QtWidgets is None:
        print("PySide6 is not installed. Install with: pipx install unicorn-studio  (or pip install PySide6)", file=sys.stderr)
        return 2
    app = QtWidgets.QApplication([sys.argv[0]] + qt_args)
    app.setApplicationName("Unicorn Studio")
    app.setDesktopFileName("unicorn-studio")
    ctl = Controller()
    win = MainWindow(ctl)
    win.show()
    ctl.connect_broker()
    return app.exec()
