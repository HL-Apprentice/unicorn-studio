# Board compatibility

| Board | Pixels | Firmware | App canvas | Rotation | Tested |
|---|---|---|---|---|---|
| Cosmic Unicorn | 32×32 | auto-detected (`cosmic` module) | 32×32 | 0/90/180/270 | **real hardware** |
| Galactic Unicorn | 53×11 | auto-detected (`galactic` module) | 53×11 | 0/180 | **not tested on hardware** (simulation only) |
| Stellar Unicorn | 16×16 | auto-detected (`stellar` module) | 16×16 | 0/90/180/270 | **not tested on hardware** (simulation only) |

**Firmware.** Pimoroni ships one MicroPython build per board; `main.py` imports whichever board module exists
and reads the size from `picographics`. Layouts adapt: the Galactic shows clock/date/agenda on one row with a
marquee, the 16×16 Stellar uses compact digits. The board reports `board`, `width`, `height` in its state.

**Uploads.** A custom display is stored as raw frames of the board's exact size. The board rejects a display of
the wrong size (`display is 32x32, this board is 53x11`), so the app and CLI always resample first.

**Scaling** (app import and CLI `show`): area-averaged resampling with
* `fit` (default): letterbox, whole picture visible (32×32 → 11×11 centred on the Galactic),
* `fill`: crop to fill the panel (32×32 → 53×53 middle band on the Galactic),
* `stretch`: distort to fill.
The app's canvas follows the connected board (Settings → "Match the connected board") or can be picked manually per board.

**Not tested on hardware:** Galactic and Stellar (the simulation covers every mode, rotation, upload and
playlist path at their sizes, but not real LED timing, buttons or audio). Galactic/Stellar button maps follow
Pimoroni's documented button names.
