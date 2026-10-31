# Privacy & security audit: Unicorn Studio 2.1.0 (firmware us-2.1)

Audit date: 2026-10-30. Scope: everything in this repository plus the private development sources it was
assembled from (which are **not** published). Method: manual code review of firmware, app, CLI and HA package;
`tools/scan-secrets.sh` (regex patterns + a private list of the real Wi-Fi/MQTT/device/name values, kept outside
the repo) over the working tree and `git log -p` of the full history; desktop simulation (`tools/fw_sim.py`)
and app self-tests; live tests on one Cosmic Unicorn. No third-party scanners were installed (gitleaks /
trufflehog would need separate approval; recommended before publishing).

Severity: High / Medium / Low / Info. Status: **Fixed**, **Open**, **Accepted**.

| # | Area | Finding | Sev | Status |
|---|---|---|---|---|
| 1 | Secrets | Private dev notes contained a plaintext broker password and home network details. None of those files are in this repo; history is fresh (single root commit); scanner reports 0 private-term and 0 pattern hits. | High | **Fixed** (excluded). Rotating that broker password is recommended because it existed in plaintext notes. |
| 2 | Secrets | Credentials now only in `secrets.py` (board), Keychain (app) or a chmod-600 `config.env` (CLI); templates `secrets.example.py`, `config.env.example`; `.gitignore` blocks real files. CLI never accepts passwords as arguments. | - | **Fixed** |
| 3 | PII | Personal names, e-mail addresses, calendar entity ids, device id, home paths, host names and the personal bundle id removed. Bundle id is `io.github.unicorn-studio`; animation `author` fields say "Unicorn Studio contributors"; HA package uses `calendar.change_me_*`. | Medium | **Fixed** |
| 4 | OTA | Old firmware accepted unsigned Wi-Fi file writes from anyone on the broker (remote code execution on the board). Now off unless `OTA_KEY` is set; HMAC-SHA256 over name+seq+body, constant-time compare, increasing sequence (no replay), `secrets.py`/`boot.py` protected, `main_prev.py` kept. | High | **Fixed** |
| 5 | Network | MQTT on 1883 is plain text on the LAN; anyone with broker write access to `unicorn/<id>/#` can change the display, upload/delete displays, reboot it. The board cannot use TLS yet (app/CLI can). | Medium | **Open**: documented in SECURITY.md (dedicated user + ACL, IoT VLAN, never expose broker). |
| 6 | Network | No listening sockets on board or Mac app (both MQTT clients; verified with `lsof` on the Mac: one outgoing TCP to 1883). No telemetry, analytics or update checks anywhere. | - | **Verified** |
| 7 | Network | Time: board reads HTTP `Date` from your HA server. Internet NTP was the default before; now only if `NTP_HOST` is set. | Low | **Fixed** |
| 8 | Network | Board publishes its LAN IP in its retained state (HA diagnostic sensor). Visible only to broker users. | Info | **Accepted** |
| 9 | Upload | Old app reported success without the board's confirmation; any `custom/result` from any client was accepted and concurrent uploads cross-signalled. New protocol v2: per-upload id, per-frame acks with retry/re-ack, busy lock, `result2 {id, ok, msg}`; app shows success only on a matching `ok`; one upload at a time; playlist sends also wait for the board's answer. | Medium | **Fixed** (verified on hardware) |
| 10 | Input (board) | MQTT packets were read whole into RAM (memory DoS / crash). Now >16 KiB packets are skipped in a streaming way (64 KiB if OTA enabled). Names `[a-z0-9_-]{1,24}`, ≤64 frames, exact frame size, durations clamped 20-60000 ms, flash-space check, playlist 1-32 items, 2-3600 s, unknown modes rejected, agenda ≤16 events with typed fields, text filtered to the font, colours/brightness clamped. | Medium | **Fixed** (fuzzed in `fw_sim.py`) |
| 11 | Input (Mac) | Import limits: 32 MiB file, 256 px side, 1000 frames, 16.7 MP decode; JSON parsed defensively; MQTT client caps packets at 1 MiB and rejects malformed lengths (self-tested). CLI caps files at 32 MiB, 64 frames, 1 MiB packets. | Low | **Fixed** |
| 12 | Mac app | Not sandboxed (reads files you open and the library folder, connects to your broker). Hardened runtime on; ad-hoc signed; not notarized (Gatekeeper right-click → Open). No entitlements requested. | Low | **Open / accepted**: sandboxing would need a Developer ID for a usable experience. |
| 13 | Mac app | Ad-hoc signature changes with every build, so macOS may re-ask for Documents, Local Network or Keychain access after an update. Found during this audit: the app read the library in `~/Documents` on the main thread, so a pending Documents prompt froze the window and delayed the board connection. Library loading is now in the background. | Low | **Fixed** (freeze) / **Accepted** (re-prompts, documented) |
| 14 | Licenses | Code MIT (`LICENSE`). Runtime deps: Pimoroni MicroPython incl. picographics/board modules (MIT), `umqtt.simple` (micropython-lib, MIT), Apple system frameworks, Python stdlib. Pimoroni's example effects (fire/rainbow/supercomputer) are **not** bundled; users copy them from Pimoroni (MIT). | - | **OK** |
| 15 | Animations | Third-party: 3 items from OpenGameArt "Holiday Expansion Set" (ZomBCool, collab. jhanson9012), **CC0 1.0**, credited. Everything else is original, proposed **CC BY 4.0** (owner to confirm; CC0 or MIT also fine). | Info | **OK** |
| 16 | Animations | **Provenance doubt: `UFO/alien_head`.** Original pixel work, recoloured blue with a starfield, but the reference image used during its creation is not recorded. Included at the owner's request; confirm it was not traced from a copyrighted image before publishing, or remove it. | Low | **Open**: owner decision |
| 17 | Animations | Excluded as fan art / third-party-derived (see below). The remaining UFO set and the Halloween icon set were generated procedurally. | - | **Fixed** (excluded) |
| 18 | Process | The automated pre-publish gate referenced by the owner's policy is not wired into this environment; this audit + scanner is the substitute. Publishing needs explicit owner approval. | Info | **Open** |

### 2.1.0 update (2026-10-31)
| # | Area | Finding | Sev. | Status |
|---|------|---------|------|--------|
| 19 | Firmware | `umqtt.simple.connect()` opens a socket with no timeout (old versions) or `settimeout(None)` (new versions): a reconnect while the network is wedged blocked the main loop past the 45 s soft watchdog → reset ("watchdog: main loop stalled"). Now every umqtt socket gets a 6 s timeout, 4 failed connects restart Wi-Fi, the read loop yields after 250 ms, and `refresh` no longer re-sends all discovery configs. The original stall was not reproduced live (6 back-to-back uploads passed), so this is the most likely cause, not a proven one. | Medium | **Fixed** (likely cause) |
| 20 | Mac app | Playlist Send uploaded missing animations and trusted a single text answer; a board reboot mid-send lost the playlist while the app looked done. Send now sends only the playlist JSON with an id, retries through reboots, and requires the board's state to list it. | Medium | **Fixed** |
| 21 | CLI | `playlist play` printed "playing" for a playlist that did not exist. It now checks the board's list and confirms the switch from state. | Low | **Fixed** |
| 22 | Firmware | New `playlist/get` returns stored items to anyone on the broker who can already control the board (same trust as every other topic). Names are validated (`good_name`), no paths. | Info | **OK** |

## Excluded animations (not in this repo)
Fan art / product graphics / third-party references: `goggle_*`, `cool_smiley*` (car-LED product graphic),
`green_baby*`, `multipass`, `element_stone*`, `neon_delta`, `stargate_*`, `ufo_alien_pilot` (stock-image
reference), the whole **Movie** and **Sci-Fi** folders. When in doubt items were excluded.

## Not tested
* Galactic (53×11) and Stellar (16×16) hardware: simulation only.
* 2.1.0 playlist UI (name/Save/picker/Send/Play, per-item speed) was compiled and its core logic self-tested;
  the SwiftUI controls were not clicked by the auditor.
* Mac app GUI clicks: the app's core upload/playlist code was exercised against the real board from a test
  driver; the SwiftUI buttons themselves were not clicked by the auditor.
* x86_64 app runtime (built and signed universal; only run on Apple silicon).
* `homeassistant/unicorn_agenda.yaml` was not loaded into a live Home Assistant.
* Board MQTT over TLS (not supported by the firmware yet).

## Before publishing (checklist)
1. Rotate the broker password that existed in private notes (finding 1).
2. Decide on `alien_head` (16) and the animation license (15).
3. Optionally run gitleaks/trufflehog (needs install approval).
4. `tools/scan-secrets.sh <private-terms-file>` must print `RESULT: clean`.
5. Set your own git author (the root commit uses a neutral placeholder).

23. **Refused playlist saves lost the app's id (fixed, us-2.1).** A rejected `playlist/save` (e.g. non-numeric speed) answered on result2 with an empty id, so the app could not match it and would have reported "board did not answer" after retries instead of the board's error. `playlist/save` now carries its id on errors too (fw_sim asserts it). Live-checked with a headless driver built on Core.swift: save→ok with id, confirmed in state.playlists, playlist/get round-trip matched the signature (incl. speed 15), bad speed refused with id, temp playlist deleted.
