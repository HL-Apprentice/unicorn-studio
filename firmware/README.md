# Firmware

`main.py`, one file for Cosmic (32×32), Galactic (53×11) and Stellar (16×16) Unicorn on Pico W, running
Pimoroni's MicroPython (which includes `picographics`, the board module and `umqtt.simple`;
if your build lacks umqtt: `mpremote mip install umqtt.simple`).

Install: see [../docs/INSTALL.md](../docs/INSTALL.md). Configuration: `secrets.example.py` → `secrets.py`.

Modes: clock, today (date), agenda, scroll_text, starfield, matrix, plasma, life, custom displays,
playlists, and Pimoroni's fire/rainbow/supercomputer if those files are on the board.
Buttons: A = fire, B = supercomputer, C = rainbow (plasma if those effects are not installed),
D = next mode, Sleep/Zzz = on/off, LUX +/- = brightness. State is kept in `settings.json`; displays in `custom/`, playlists in `playlists/`.

Test without hardware: `python3 ../tools/fw_sim.py`.
Backup a board: `mpremote fs cp -r :. ./backup/` (contains your `secrets.py`; keep it private).
