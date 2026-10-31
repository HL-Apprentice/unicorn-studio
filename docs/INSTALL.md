# Install

## 1. Board firmware (USB)
1. Download Pimoroni's MicroPython **for your board** (UF2) from
   https://github.com/pimoroni/unicorn/releases (Cosmic, Galactic and Stellar have separate files; pick the
   Pico W build).
2. Hold **BOOTSEL**, plug the board in, drag the `.uf2` onto the `RPI-RP2` drive.
3. Copy `firmware/secrets.example.py` to `secrets.py`, fill in Wi-Fi and MQTT, then (with
   `pip install mpremote`):
   ```
   mpremote fs cp firmware/main.py :main.py
   mpremote fs cp secrets.py :secrets.py
   mpremote reset
   ```
4. The board shows the clock and appears in Home Assistant (MQTT integration) as *Cosmic/Galactic/Stellar
   Unicorn*. Its topic base is `unicorn/<last 6 hex digits of its MAC>`.

Optional: copy Pimoroni's example effects `fire.py`, `rainbow.py`, `supercomputer.py` next to `main.py`
to unlock those modes (they are Pimoroni's MIT-licensed examples and are not bundled here).

Updates later: USB again, or set `OTA_KEY` on the board and in the CLI config and run
`unicorn push main.py` (HMAC-signed, replay-protected; without `OTA_KEY` Wi-Fi updates are refused).

## 2. Mac app
1. Download `Unicorn-Studio-<version>-macOS-universal.zip` from the release, unzip, move
   **Unicorn Studio.app** to Applications.
2. **First launch:** the app is ad-hoc signed and *not notarized*, so a double-click is blocked.
   **Right-click (or Control-click) the app → Open → Open.** macOS remembers the choice.
   On macOS 15+, if there is no Open button: try to open it once, then
   **System Settings → Privacy & Security → "Open Anyway"**.
   Check the download first if you like: `shasum -a 256 -c Unicorn-Studio-*.zip.sha256`.
3. Settings: broker host, port, user, password (stored in the Keychain), TLS. Pick your board from the
   discovered list. Allow "Local Network" access (to reach the broker) and "Documents" access (your
   library lives in `~/Documents/Unicorn Displays`) when macOS asks. Because the app is ad-hoc signed, macOS may
   ask again after each update.

Build it yourself instead (Xcode command line tools, no Xcode project needed):
```
cd mac-app && ./build.sh            # universal; ARCHS=arm64 ./build.sh for one arch
```
The zip lands in `mac-app/dist/`; `INSTALL=1 ./build.sh` also installs to `~/Applications`.

## 3. CLI (optional)
```
install -m 755 cli/unicorn ~/.local/bin/unicorn
mkdir -p ~/.config/unicorn && cp cli/config.env.example ~/.config/unicorn/config.env
chmod 600 ~/.config/unicorn/config.env   # then edit it
unicorn boards && unicorn status
```
Python 3.8+, standard library only.

## 4. Home Assistant (optional, for the agenda view)
See [homeassistant/README.md](../homeassistant/README.md).

## 5. Linux app
1. Install pipx if you do not have it, for example `sudo apt install pipx` (Debian/Ubuntu) or
   `sudo dnf install pipx` (Fedora).
2. Download `unicorn-studio-2.1.0-linux.tar.gz` and `SHA256SUMS-linux.txt` from the release, check and install:
   ```
   sha256sum -c SHA256SUMS-linux.txt --ignore-missing
   tar xzf unicorn-studio-2.1.0-linux.tar.gz && cd unicorn-studio-2.1.0-linux && ./install.sh
   ```
   `install.sh` runs `pipx install` on the wheel (or uses a venv in `~/.local/share/unicorn-studio` without pipx).
   It needs no root and adds **Unicorn Studio** to your applications menu.
3. Start the app, open **Settings…** and enter the broker host, port, MQTT user and password; tick TLS if your
   broker uses it (usually port 8883).
4. **Where the password is kept.** It is read from `MQTT_PASSWORD` in the environment, or from the system keyring
   (optional: `pipx inject unicorn-studio keyring`, then tick **Remember password**), or from a config file you
   create:
   ```
   mkdir -p ~/.config/unicorn-studio
   cp config.env.example ~/.config/unicorn-studio/config.env
   chmod 600 ~/.config/unicorn-studio/config.env      # then edit it
   ```
   The app also reads the CLI's `~/.config/unicorn/config.env`. `unicorn-studio --check-config` shows which settings
   were found and prints only "set" or "not set" for the password. `settings.json` never holds the password.
5. **Playlists.** Upload displays first (Library or Editor tab, **⬆ Upload to board**). Then in the Board tab use
   **On the board → ＋ Add to playlist**, add built-in modes in the Playlists tab, set seconds and speed, type a
   name, **Save**, **④ Send to board**, **⑤ ▶ Play on board**. Send transfers only the playlist; displays missing
   from the board are flagged ⚠ and skipped by the board.
6. **The library** lives in `~/Documents/Unicorn Displays` by default (your XDG Documents folder). Change it in
   Settings or with `UNICORN_LIBRARY`.

Requirements: Python 3.9+; glibc 2.28+ on x86_64 or 2.31+ on arm64 (PySide6 wheels); X11 or Wayland. On minimal
installs Qt may also need `libegl1`, `libxkbcommon-x11-0` and `libxcb-cursor0`.

Uninstall: `pipx uninstall unicorn-studio`, then `rm ~/.local/share/applications/unicorn-studio.desktop`.

Build from source: `linux-app/packaging/build.sh` writes the wheel, the sdist, the Linux tar.gz and
`SHA256SUMS-linux.txt` to `dist/`, bundling `animations/json` as the first-run library.
