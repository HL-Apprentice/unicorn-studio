# Security

## Design
* **Local only.** Board, app and CLI talk only to *your* MQTT broker (and the board reads the time from your
  HA server's HTTP `Date` header, optionally your own NTP server). No cloud, telemetry, analytics or update
  checks. Nothing listens on a port: the board and the app are MQTT *clients*.
* **Credentials** live in `secrets.py` on the board, the macOS Keychain (app) or a `chmod 600` config file
  (CLI). None are in this repository; `.gitignore` blocks `secrets.py` and `config.env`.
* **Uploads are validated on the board:** names `[a-z0-9_-]{1,24}`, ≤64 frames, exact frame size, durations
  clamped, free-flash check, MQTT packets >16 KiB dropped without buffering, text sanitised to the font.
* **Wi-Fi file updates are off** unless you set `OTA_KEY`; then every file must carry an HMAC-SHA256 signature
  and a rising sequence number, and `secrets.py`/`boot.py` can never be replaced.
* **The app** is signed ad-hoc with the hardened runtime, not sandboxed (it reads files you open and your
  library folder). Image/JSON import is size-limited (32 MiB, 256 px per side, 1000 frames, 16 MP decode).

## Recommended broker setup
MQTT on port 1883 is plain text on your LAN. Use a dedicated, non-admin user for the board and app and limit
it with a Mosquitto ACL (HA add-on: `customize` → `acl_file`):
```
user unicorn
topic readwrite unicorn/#
topic write homeassistant/#
topic read homeassistant/status
```
For TLS, enable port 8883 on the broker and set `MQTT_TLS=1` / the app's TLS switch (the board itself
currently connects without TLS; keep it on a trusted or IoT VLAN).
Anyone who can publish to `unicorn/<id>/...` can change what the board shows, so do not expose the broker to
the internet.

## Reporting
Please open a private security advisory on the GitHub repository (Security → Report a vulnerability).
