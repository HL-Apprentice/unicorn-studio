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
* **Playlists:** made only from displays already on the board plus built-in modes; explicit name + **Save**;
  picker lists playlists saved on this Mac and those only on the board (imported on selection); per-item seconds
  and speed; **Send to board** sends only the playlist (never animations) and is confirmed from the board's state.
* **Settings:** broker, port, user, password (Keychain), TLS, board picker (discovered on the broker).
  Optional `config.env` (see `config.env.example`).

Build: `./build.sh` (universal, ad-hoc signed, hardened runtime, zipped to `dist/` with a SHA-256).
Self-test: `xcrun swiftc -parse-as-library Sources/Core.swift selftest/main.swift -o /tmp/ust && /tmp/ust`.

## Editing playlists
Open the **Playlists** tab (top of the right-hand panel). The steps are printed at the top of the tab:

1. **Add items.** Board tab → under "On the board" press ＋ on a display; or in the Playlists tab pick a
   built-in mode or an on-board display in **Add** and press **Add**. Only displays already on the board can be
   added. A new animation must be uploaded first (open it in the Library → *Send to Unicorn*). Items that are
   not on the board (e.g. deleted later) are shown in orange with ⚠︎ and are skipped by the board.
2. **Name it** in the **Name** field.
3. **Save** (stores it on this Mac). The status line says *Not saved yet*, *Unsaved changes*, *Saved on this
   Mac only*, *On the board, but this Mac's copy is different*, or *✓ Saved on this Mac and on the board*.
4. **Send to board** sends only the playlist. It waits for the board if it is offline or rebooting, retries,
   and reports success only after the board answers *ok* and its own playlist list contains the name.
5. **▶︎ Play** starts it (confirmed from the board's state). **Send & play** does 4 + 5.

* **Open** picker: *Saved on this Mac* (marked "also on the board" or "Mac only") and *Only on the board*
  (selecting one imports its items from the board; firmware us-2.1+, then press Save to keep a copy).
  ＋ = new playlist; the bin deletes the Mac copy (asks first); **Remove from board** deletes the board copy.
  Changing the name of a saved playlist offers **Rename** (this Mac) or **Save copy**.
* **Per item:** seconds (2-3600) and **Speed** 1-20: 10 = normal (as the animation was made), lower = slower,
  higher = faster. New items start at 10; playlists saved before per-item speed open with 10.
* **Remove:** the − button, right-click → *Remove from playlist*, or select rows and press **Delete**.
  **Reorder:** drag a row by its ≡ handle. Switching playlists with unsaved changes asks first.
