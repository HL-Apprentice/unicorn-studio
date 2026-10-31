# Unicorn Studio

Paint, animate and schedule displays for the Pimoroni **Cosmic (32×32)**, **Galactic (53×11)** and
**Stellar (16×16)** Unicorn boards (Raspberry Pi Pico W), all over your *local* MQTT broker.

> **Hardware testing:** only the **Cosmic Unicorn** has been tested on a real board. **Galactic and Stellar support has NOT been tested on hardware** yet; it runs in desktop simulation only. Reports and fixes are welcome; see [docs/BOARDS.md](docs/BOARDS.md).

| Part | Folder | What it does |
|---|---|---|
| Firmware | `firmware/` | One MicroPython `main.py` for all three boards (auto-detects the board). Clock, date, calendar agenda, scrolling text, effects, uploaded custom displays, **playlists**, Home Assistant MQTT discovery. |
| Mac app | `mac-app/` | **Unicorn Studio.app** (SwiftUI, macOS 13+): pixel editor, GIF/PNG import with fit/fill/stretch scaling, per-board canvas, library, "On the board" list (Play / Delete / Refresh, current item highlighted), playlist editor. |
| Linux app | `linux-app/` | **Unicorn Studio for Linux** (Python + Qt 6, x86_64 and arm64): pixel editor with live mirror, GIF/PNG import, library, "On the board" list, playlist editor with per-item speed and import from the board. See [linux-app/README.md](linux-app/README.md). |
| CLI | `cli/unicorn` | Python 3 stdlib only: status, modes, upload display JSON (auto-resized), playlists, file push (signed Wi-Fi or USB). |
| Home Assistant | `homeassistant/` | Package YAML that publishes your calendar as a retained agenda message; the board itself appears via MQTT discovery (light, mode select incl. playlists, rotation, text, speed, sensors). |
| Animations | `animations/` | 47 ready-made displays (JSON for the app/CLI, GIF previews). See `animations/CREDITS.md`. |

Nothing talks to the internet: no cloud, no telemetry, no open ports on the Mac, the Linux PC or the board.

## Quick start
1. Flash Pimoroni's MicroPython build for your board, copy `firmware/main.py` and your `secrets.py`
   (from `firmware/secrets.example.py`) to it. → [docs/INSTALL.md](docs/INSTALL.md)
2. In Home Assistant the board appears under **Settings → Devices → MQTT**.
3. Install the app from the release zip (right-click → **Open** the first time, it is ad-hoc signed, not notarized).
4. On Linux: `tar xzf unicorn-studio-2.1.0-linux.tar.gz && cd unicorn-studio-2.1.0-linux && ./install.sh`
   (pipx, no root). → [docs/INSTALL.md](docs/INSTALL.md#5-linux-app)
5. Optional: `cli/unicorn` for scripting, `homeassistant/unicorn_agenda.yaml` for the calendar view.

Docs: [INSTALL](docs/INSTALL.md) · [BOARDS](docs/BOARDS.md) · [PROTOCOL](docs/PROTOCOL.md) · [WLED](docs/WLED.md) ·
[SECURITY](SECURITY.md) · [AUDIT](AUDIT.md)

## Linux app
The Linux app does what the Mac app does: board status and controls, the "On the board" list, the pixel editor
(pencil, eraser, fill, eyedropper, text, colour picker, frames with per-frame timing, undo, onion skin, preview,
Save / Save As, explicit Upload) with the live mirror, the library with GIF/PNG import, confirmed v2 uploads, and the
playlist editor (displays already on the board plus built-in modes, seconds and speed per item, name, Save, Send to
board, Play; Send transfers only the playlist, never displays; board-only playlists can be imported).
It runs on x86_64 and arm64 (for example a Raspberry Pi 4/5) with Python 3.9+ and pipx.

Testing status: the Linux app has been tested only in desktop simulation (the real firmware code, simulated hardware
and a local Mosquitto broker, for Cosmic, Galactic and Stellar) under Xvfb on x86_64. It has not been tested on
hardware. (Galactic/Stellar not tested on hardware.)

## Tests
* `python3 tools/fw_sim.py` runs the firmware on desktop Python with mocked hardware for all three board sizes
  (every mode and rotation, the upload protocol, playlists, OTA signature checks, hostile input).
* `mac-app/selftest/` exercises the app's core (codec, resampling, MQTT framing, upload protocol).
* `linux-app/tests/` runs the Linux app against the real `firmware/main.py` (simulated hardware, local Mosquitto)
  for all three boards: unit tests, end-to-end protocol tests and a GUI test under Xvfb. See
  [linux-app/README.md](linux-app/README.md#testing-status).
* `tools/scan-secrets.sh` checks the working tree and git history for credentials and private data.

## License
* **Code** (firmware, macOS app, Linux app, CLI, tools, Home Assistant files): MIT, see [`LICENSE`](LICENSE).
* **Animations and display assets** (`animations/`): [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/),
  see [`LICENSE-ASSETS`](LICENSE-ASSETS) and [`animations/LICENSE.md`](animations/LICENSE.md). Three holiday
  animations are third-party CC0 1.0 works, credited in [`animations/CREDITS.md`](animations/CREDITS.md).
* Provenance and third-party notices: [`NOTICE`](NOTICE).

## Credits
Made by HL-Apprentice with an AI assistant (code, firmware, animations). See [`CONTRIBUTORS.md`](CONTRIBUTORS.md).
Not affiliated with Pimoroni. "Cosmic/Galactic/Stellar Unicorn" are Pimoroni product names.
