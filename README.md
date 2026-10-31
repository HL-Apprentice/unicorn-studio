# Unicorn Studio

Paint, animate and schedule displays for the Pimoroni **Cosmic (32×32)**, **Galactic (53×11)** and
**Stellar (16×16)** Unicorn boards (Raspberry Pi Pico W), all over your *local* MQTT broker.

> **Hardware testing:** only the **Cosmic Unicorn** has been tested on a real board. **Galactic and Stellar support has NOT been tested on hardware** yet; it runs in desktop simulation only. Reports and fixes are welcome; see [docs/BOARDS.md](docs/BOARDS.md).

| Part | Folder | What it does |
|---|---|---|
| Firmware | `firmware/` | One MicroPython `main.py` for all three boards (auto-detects the board). Clock, date, calendar agenda, scrolling text, effects, uploaded custom displays, **playlists**, Home Assistant MQTT discovery. |
| Mac app | `mac-app/` | **Unicorn Studio.app** (SwiftUI, macOS 13+): pixel editor, GIF/PNG import with fit/fill/stretch scaling, per-board canvas, library, "On the board" list (Play / Delete / Refresh, current item highlighted), playlist editor. |
| CLI | `cli/unicorn` | Python 3 stdlib only: status, modes, upload display JSON (auto-resized), playlists, file push (signed Wi-Fi or USB). |
| Home Assistant | `homeassistant/` | Package YAML that publishes your calendar as a retained agenda message; the board itself appears via MQTT discovery (light, mode select incl. playlists, rotation, text, speed, sensors). |
| Animations | `animations/` | 47 ready-made displays (JSON for the app/CLI, GIF previews). See `animations/CREDITS.md`. |

Nothing talks to the internet: no cloud, no telemetry, no open ports on the Mac or the board.

## Quick start
1. Flash Pimoroni's MicroPython build for your board, copy `firmware/main.py` and your `secrets.py`
   (from `firmware/secrets.example.py`) to it. → [docs/INSTALL.md](docs/INSTALL.md)
2. In Home Assistant the board appears under **Settings → Devices → MQTT**.
3. Install the app from the release zip (right-click → **Open** the first time, it is ad-hoc signed, not notarized).
4. Optional: `cli/unicorn` for scripting, `homeassistant/unicorn_agenda.yaml` for the calendar view.

Docs: [INSTALL](docs/INSTALL.md) · [BOARDS](docs/BOARDS.md) · [PROTOCOL](docs/PROTOCOL.md) · [WLED](docs/WLED.md) ·
[SECURITY](SECURITY.md) · [AUDIT](AUDIT.md)

## Tests
* `python3 tools/fw_sim.py` runs the firmware on desktop Python with mocked hardware for all three board sizes
  (every mode and rotation, the upload protocol, playlists, OTA signature checks, hostile input).
* `mac-app/selftest/` exercises the app's core (codec, resampling, MQTT framing, upload protocol).
* `tools/scan-secrets.sh` checks the working tree and git history for credentials and private data.

## License
Code: MIT (see `LICENSE`). Animations: see `animations/CREDITS.md`.
Not affiliated with Pimoroni. "Cosmic/Galactic/Stellar Unicorn" are Pimoroni product names.
