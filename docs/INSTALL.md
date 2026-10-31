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
