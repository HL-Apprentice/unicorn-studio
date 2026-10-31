# Unicorn Studio (macOS app)

SwiftUI app, macOS 13+, no third-party dependencies (Foundation, Network, ImageIO, Security, SwiftUI).

* **Canvas per board:** 32×32 Cosmic, 53×11 Galactic, 16×16 Stellar; follows the connected board
  ("Match the connected board") or pick one. Pixel editor with frames and durations.
* **Import** GIF/PNG/JPEG or display JSON with **Fit** (letterbox), **Fill** (crop) or **Stretch**.
* **Library:** bundled animations plus your own folder (`~/Documents/Unicorn Displays`, or `$UNICORN_LIBRARY`).
* **On the board:** every stored display and playlist with **Play**, **Delete**, the playing item highlighted,
  a **Refresh** button, and automatic refresh after upload/delete/play and on launch/reconnect.
* **Upload** uses protocol v2 (see `docs/PROTOCOL.md`): success is shown only when the board confirms with a
  matching `ok`; otherwise the board's error is shown in red. One upload at a time.
* **Playlists:** create, rename, delete, add library displays (uploaded automatically if missing on the board)
  and built-in modes, drag to reorder, seconds per item, Send / Activate, current item shown.
* **Settings:** broker, port, user, password (Keychain), TLS, board picker (discovered on the broker).
  Optional `config.env` (see `config.env.example`).

Build: `./build.sh` (universal, ad-hoc signed, hardened runtime, zipped to `dist/` with a SHA-256).
Self-test: `xcrun swiftc -parse-as-library Sources/Core.swift selftest/main.swift -o /tmp/ust && /tmp/ust`.

## Editing playlists
Open the **Playlists** tab (top of the right-hand panel) and pick a playlist (＋ makes a new one, the bin
deletes it here and on the board, type a name and press **Rename**).
* **Add from the board:** in the **Board** tab, each display under "On the board" has a ＋ (list) button, or
  right-click it → *Add to playlist "…"*. It is added to the playlist currently selected in the Playlists tab.
* **Add from the library / modes:** right-click a library item → *Add to playlist*; or choose a built-in mode
  (clock, agenda, date…) and press **Add mode**.
* **Remove:** the − button on the row, right-click → *Remove from playlist*, or select rows and press **Delete**.
* **Seconds:** type in the box next to each item (Return to apply) or use its up/down stepper (2-3600 s).
* **Reorder:** drag a row by its ≡ handle.
* **Apply:** edits are saved on the Mac immediately (an orange note says they are not on the board yet).
  **Send & play on the board** uploads any missing displays, stores the playlist on the board and starts it;
  **Send only** stores it without switching. Success is shown only after the board confirms.
