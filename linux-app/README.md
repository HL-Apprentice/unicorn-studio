# Unicorn Studio for Linux

A desktop app for Pimoroni **Cosmic** (32×32), **Galactic** (53×11) and **Stellar** (16×16) Unicorn boards
running the Unicorn Studio firmware (`us-2.1` recommended; `us-2.0` works without playlist import and save ids). It talks to the board through your local MQTT broker
(for example the Mosquitto add-on in Home Assistant), using the same protocol as the Mac app and the CLI
([docs/PROTOCOL.md](../docs/PROTOCOL.md)).

Written in Python with Qt 6 (PySide6). It is a pure-Python package, so the same file installs on x86_64 and arm64
(for example a Raspberry Pi 4/5 running 64-bit Raspberry Pi OS). PySide6 (6.8 or newer) comes from PyPI.
- x86_64 needs glibc 2.28 or newer.
- arm64 needs glibc 2.31 or newer, for example Raspberry Pi OS Bookworm or Ubuntu 22.04. pip picks the newest
  PySide6 your system supports.

## What it does

- **Connect** to your MQTT broker (TCP or TLS, user and password). It finds boards by listening on `unicorn/+/state`
  and uses the first one it finds. If you have more than one, pick it from the **Board** menu.
- **Board tab**: status (board type, size, firmware, uptime, last reset), on/off, brightness, mode, rotation
  (0/90/180/270 on square boards, 0/180 on Galactic), scrolling text and speed.
- **On the board**: the displays and playlists stored on the board. The one playing is highlighted.
  - **Play**, **＋ Add to playlist**, **Delete** and **↻ Refresh** are buttons. The same actions are in the right-click menu.
  - Double-click a row to play it. Press the Delete key to delete the selected row.
  - The list refreshes by itself when you connect and after every upload, delete or play.
- **Editor tab** (pixel editor, same tools as the Mac app):
  - the canvas follows the connected board (32×32, 53×11 or 16×16), or pick a board size in **Canvas**;
    resizing uses fit, fill or stretch;
  - tools: **Pencil**, **Erase**, **Fill**, **Pick colour** (eyedropper) and **Text** (stamps a small 3×5 font);
  - colour picker, a 16-colour palette and the last 8 colours used;
  - **Undo** (60 steps), shift ← → ↑ ↓, **Flip**, **Clear**, **Onion skin**;
  - frames: **＋ Add**, **Duplicate**, **Delete**, **◀ Move / Move ▶**, **▶ Play** preview, milliseconds per frame
    (20–60000) for one frame or **all**; the board takes at most 64 frames;
  - **New**, **Save** and **Save As…** write a standard display `.json` into the library. **Open in editor** in the
    Library tab, or a double-click there, loads one back;
  - **⬆ Upload to board** is the only way the editor sends a display to the board;
  - **Live (mirror the canvas)** shows the canvas on the board as you draw (at most 4 updates a second). It is only
    available when the canvas is the board's size. Nothing is stored on the board, and the board returns to its
    previous display when you turn Live off.
- **Library tab**: your display files (`.json`) with an animated preview.
  - Imports GIF, PNG and JPEG files at the board's size.
  - Displays made for another size are scaled with **fit** (letterbox), **fill** (crop) or **stretch**.
  - **⬆ Upload to board** uploads the selected display. Tick **Play it after the upload** to start it as well.
- **Uploads** use protocol v2:
  - every upload has its own id;
  - the board acknowledges each frame, and lost frames are sent again;
  - only one upload runs at a time;
  - success is reported **only** after the board confirms it saved the display. Otherwise you see `✗ Not saved: …`
    with the reason.
- **Playlists tab**. A playlist only refers to displays that are **already on the board**:
  1. Add displays from the Board tab (**On the board** → **＋ Add to playlist**) and built-in modes (clock, date,
     agenda and the others) with **＋ Add mode**. Displays cannot be added from the library: upload them first.
  2. Type a **Name** (lowercase letters, digits, `_` and `-`, up to 24).
  3. **Save** keeps it on this computer (in `Playlists/` inside the library).
  4. **④ Send to board** sends only the playlist (names, seconds and speeds). It never uploads displays. A display that
     is not on the board is marked **⚠ not on the board** and the board skips it until you upload it.
  5. **⑤ ▶ Play on board** starts it.
  - Success is reported only when the board replies `ok playlist …` (on us-2.1, in `playlist/result2` with the id
    this save sent) **and** the playlist shows up in the board's own playlist list. Play is reported only when the board says it is playing that playlist.
  - Each row has **Seconds** (2–3600) and **Speed** (1–20; **Normal (10)** = the display's own frame timing). The board
    uses an item's speed while that item plays and its global speed otherwise. Double-click a cell to change it.
  - Reorder by dragging rows or with **↑ ↓**. Remove with **− Remove** or the Delete key.
  - The **Playlist** menu lists the playlists on this computer and the ones only on the board. **Import from board**
    makes a local copy of a board-only playlist, items, seconds and speeds included (`playlist/get`, us-2.1). With
    us-2.0 firmware the board reports only the name, so the copy starts empty.
  - The Board tab shows the item speed in effect while a playlist item with its own speed plays (`speed_now`).
  - Send is enabled only after Save; Play only when the board has the playlist and there are no unsaved changes.

## Install

You need Python 3.9 or newer and either `pipx` (recommended) or `python3-venv`.

```bash
# from the release archive
tar xzf unicorn-studio-2.1.0-linux.tar.gz
cd unicorn-studio-2.1.0-linux
./install.sh                 # pipx install + adds "Unicorn Studio" to your app menu
```

Or by hand:

```bash
pipx install ./unicorn_studio-2.1.0-py3-none-any.whl
unicorn-studio --install-desktop      # .desktop file + icon in ~/.local/share
unicorn-studio
```

Qt needs the usual desktop libraries. These are already present on GNOME and KDE desktops. On a minimal system you may
need to install them: `libegl1`, `libxkbcommon-x11-0` and `libxcb-cursor0` (Debian/Ubuntu names). Wayland and X11
both work through Qt.

Uninstall: `pipx uninstall unicorn-studio`, then delete `~/.local/share/applications/unicorn-studio.desktop`.

## Settings and credentials

Open **Settings…** and enter the broker, port, user and password. The app writes only non-secret settings to
`~/.config/unicorn-studio/settings.json` (mode 600): host, port, user, board, TLS and library folder.
The password is never written there and never logged.

The password comes from one of these, in this order:

1. the `MQTT_PASSWORD` environment variable;
2. the system keyring (GNOME Keyring or KWallet), if you install the optional extra
   (`pipx inject unicorn-studio keyring`) and tick **Remember password**;
3. a config file: `~/.config/unicorn-studio/config.env`, or the CLI's `~/.config/unicorn/config.env`,
   or `$UNICORN_CONFIG`. See [config.env.example](config.env.example).
   - Keep the file `chmod 600`.
   - It can point at another file with `SECRETS_FILE=...`.

Without one of these, a password typed in Settings is kept for that session only.

`unicorn-studio --check-config` shows where each setting comes from. It prints only "set" or "not set" for the password.

**Library folder**, in this order:

1. `$UNICORN_LIBRARY`;
2. the folder chosen in Settings;
3. `Unicorn Displays` inside your XDG Documents folder (`xdg-user-dir DOCUMENTS`), usually
   `~/Documents/Unicorn Displays`.

Playlists are stored in `Playlists/` inside the library. On first start an empty library is filled with the
animations bundled in the release, if any.

The app has no telemetry and makes no network connections except to your MQTT broker.

## Testing status

Tested on Debian 13 (x86_64) with Python 3.13, using PySide6 6.12.0 (all tests). PySide6 6.8.0.2 was tested
in an earlier round, before the editor, per-item speed and playlist import were added. The repo's real
`firmware/main.py` (us-2.1), with simulated hardware
(`tests/board_sim.py`, built on `tools/fw_sim.py`), was connected to a local Mosquitto 2.0.21 broker for each of
Cosmic, Galactic and Stellar:

- `tests/test_core.py`: config, file parsing, scaling, playlist (seconds, speed, no file paths) and pixel-editor
  model unit tests (line, fill, undo, eyedropper, frames, text, save/load, resize, live chunks).
- `tests/test_e2e.py`: status, controls, rotation limits, uploads checked byte-for-byte on the simulated flash,
  lost-chunk retry, refusal of a second upload, board "busy", offline board, playlist save/play/delete, reboot
  persistence, per-item speed stored and applied (`speed_now`), save ids matched in `playlist/result2`,
  `playlist/get` read-back.
- `tests/gui_smoke.py`: the real window under Xvfb, driven by code, covering every button above: the playlist flow
  (add from the board, ⚠ flag, seconds and speed, Save, Send, check of the board's flash and playlist list, Play,
  import of a board-only playlist with its items), and the editor (draw with mouse events, fill, undo, eyedropper, text, frames,
  onion skin, playback, Save As, reload, Upload checked byte-for-byte on the simulated flash, Live on and off,
  canvas sizes).

**Not tested:**

- **Real hardware.** The Linux app has not been run against a real Unicorn board.
- **Galactic and Stellar** have not been tested on hardware with any app; they are desktop simulation only.
- **Other brokers.** It has not been tried with Home Assistant's broker or with TLS.
- **arm64.** It has not been run on arm64. The package is architecture-independent and PySide6 publishes arm64
  wheels (checked on PyPI: 6.8.0.2 for glibc 2.31+, 6.12.0 for glibc 2.39+), but no arm64 machine was used.
- **Wayland** sessions and real desktop environments. The GUI was tested under Xvfb (X11) only.
- **The keyring option.**
- **us-2.0 firmware.** The fallbacks for older firmware (text-only save reply, empty import) are unit-tested only.
- **Drawing by hand.** Editor strokes were simulated mouse events; the system colour dialog was not opened in tests.
- **Live mirror timing** on real hardware (frame rate, flicker).

## Run from source / tests

```bash
cd linux-app
python3 -m venv .venv && .venv/bin/pip install 'PySide6-Essentials>=6.8'
.venv/bin/python -m unicorn_studio
# tests (need mosquitto + mosquitto_passwd on PATH, or MOSQUITTO=/path/to/mosquitto)
.venv/bin/python tests/test_core.py
.venv/bin/python tests/test_e2e.py
xvfb-run -a .venv/bin/python tests/gui_smoke.py
packaging/build.sh            # wheel + sdist + tar.gz + SHA256SUMS-linux.txt in ../dist
```
