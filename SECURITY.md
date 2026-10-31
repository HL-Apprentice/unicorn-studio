# Security

## Design
* **Local only.** Board, app and CLI talk only to *your* MQTT broker (and the board reads the time from your
  HA server's HTTP `Date` header, optionally your own NTP server). No cloud, telemetry, analytics or update
  checks. Nothing listens on a port: the board and the app are MQTT *clients*.
* **Credentials** live in `secrets.py` on the board, the macOS Keychain (app) or a `chmod 600` config file
  (CLI). None are in this repository; `.gitignore` blocks `secrets.py` and `config.env`.
* **Uploads are validated on the board:** names `[a-z0-9_-]{1,24}`, ≤64 frames, exact frame size, durations
  clamped, free-flash check, MQTT packets over 16 KiB (64 KiB when `OTA_KEY` is set) dropped without
  buffering, text sanitised to the font.
* **Wi-Fi file updates are off** unless you set `OTA_KEY`; then every file must carry an HMAC-SHA256 signature
  and a rising sequence number. An update can replace `main.py`, which runs at boot with full access to the
  board (including `secrets.py`), so **anyone with `OTA_KEY` controls the board**: treat it like a password,
  and leave it unset if you only update over USB. Refusing writes to `secrets.py`/`boot.py` prevents accidents;
  it is not a security boundary.
* **Calendar data.** The Home Assistant agenda package publishes your event titles as a *retained* message on
  `unicorn/agenda`, so the broker keeps them and any client allowed to read `unicorn/#` can see them. Keep that
  permission to the board/app user (see the ACL below), or leave the package out.
* **The app** is signed ad-hoc with the hardened runtime, not sandboxed (it reads files you open and your
  library folder). Image/JSON import is size-limited (32 MiB, 256 px per side, 1000 frames, 16 MP decode).

## Recommended broker setup
MQTT on port 1883 is plain text on your LAN. Use a dedicated, non-admin user for the board and app and limit
it with a Mosquitto ACL (HA add-on: `customize` → `acl_file`):
```
user unicorn
topic readwrite unicorn/#
topic read homeassistant/status

# Home Assistant discovery: each board may write only its own config topics
# (homeassistant/<component>/<client id>/<entity>/config, where the client id is unicorn_<id>).
pattern write homeassistant/+/%c/+/config
```
Mosquitto applies `pattern` lines to every client, but `%c` limits each one to topics carrying its own client
id, so the board can no longer rewrite the discovery config of other Home Assistant devices.

**Upgrading from us-2.1 or older:** flash us-2.2 *before* tightening the ACL. On its first connection us-2.2
clears its old discovery topics (`homeassistant/<component>/unicorn_<id>[_<entity>]/config`) and republishes
under the new layout. If the ACL is already tightened, that clean-up is refused and Home Assistant keeps the old
entries: delete the duplicate Unicorn device under Settings → Devices & services → MQTT.
For TLS, enable port 8883 on the broker and set `MQTT_TLS=1` / the app's TLS switch (the board itself
currently connects without TLS; keep it on a trusted or IoT VLAN).
Anyone who can publish to `unicorn/<id>/...` can change what the board shows, so do not expose the broker to
the internet.

## Reporting
Please open a private security advisory on the GitHub repository (Security → Report a vulnerability).
