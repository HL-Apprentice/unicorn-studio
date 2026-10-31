# MQTT protocol (firmware us-2.x)

Base topic `B = unicorn/<id>` where `<id>` is the last 6 hex digits of the board's MAC. MQTT 3.1.1, QoS 0.

| Topic | Dir | Payload |
|---|---|---|
| `B/status` | board → | `online` / `offline` (retained, LWT) |
| `B/state` | board → | JSON (retained): `state, brightness, color, mode, effect, customs[], playlists[], modes[], playing, rotation, text, speed, ip, board, width, height, uptime, mem_free, time_synced, version, ota, reset_reason` |
| `B/light/set` | → board | HA JSON light schema: `state`, `brightness` 0-255, `color{r,g,b}`, `effect` |
| `B/mode/set` | → board | `clock`, `today` (alias `date`), `agenda`, `scroll_text`, `starfield`, `matrix`, `plasma`, `life`, `fire`/`rainbow`/`supercomputer` (if installed), `custom:<name>`, `playlist:<name>` |
| `B/rotation/set`, `B/text/set`, `B/speed/set`, `B/reboot/set`, `B/refresh/set` | → board | value; `refresh` republishes state |
| `unicorn/agenda` | HA → all boards | retained JSON `{"now":epoch,"tz":utc_offset_s,"ev":[{"t":title,"s":start,"e":end,"c":0..2}]}` (max 16 events) |
| `homeassistant/status` | HA → | `online` triggers re-discovery |

## Custom display upload (v2)
Names: 1-24 chars of `a-z 0-9 _ -`. Max 64 frames. Frames are exactly `W×H×3` bytes RGB, row-major, upright.
Chunk size 1024 bytes, `NCH = ceil(W*H*3/1024)` chunks per frame.

1. `B/custom/begin` JSON `{"name","id","width","height","durations":[ms,...]}`; `id` = up to 16 chars, unique per upload.
   A different `id` while another upload is active (<idle timeout) → `busy`.
2. Per frame, per chunk: `B/custom/frame` = `[0xA5, len(id), id…, frame_hi, frame_lo, chunk] + data`.
   After the last chunk of each frame the board writes it to flash and publishes `B/custom/ack` = `"<id>:<frames done>"`.
   The sender waits for that ack (retrying the frame on timeout; a repeated frame is re-acked, not re-written).
3. `B/custom/end` JSON `{"name","id","play":true|false}`.
4. Result: `B/custom/result2` JSON `{"id","ok":bool,"msg"}` (plus legacy text on `B/custom/result`).
   **A sender must only report success on `result2` with its own `id` and `ok:true`.**

`B/custom/delete` = name. `B/custom/live` = `[chunk]+data` streams an unsaved preview (empty payload ends it).
v1 (no `0xA5` header, text acks) is still accepted for old clients.

## Playlists
`B/playlist/save` JSON `{"name":"evening","items":[{"mode":"custom:alien_head","seconds":10},{"mode":"clock","seconds":10}]}`
(also accepted: `{"type":"custom","name":"alien_head"}`; seconds clamp 2-3600; 1-32 items).
Stored as `playlists/<name>.json`, survives reboots, runs on the board alone. Play with `B/mode/set` =
`playlist:<name>`. Items whose custom display is missing are skipped; if nothing is playable the board shows
`EMPTY`. `B/playlist/delete` = name. Results on `B/playlist/result2`. `state.playing` = current item.

## Signed file update (optional)
Only if `OTA_KEY` is set in `secrets.py`. `B/ota/put` = `name\nseq\nhex(HMAC-SHA256(OTA_KEY, name+"\n"+seq+"\n"+body))\nbody`;
`seq` must increase; `secrets.py`/`boot.py` can't be written; `main.py` is kept as `main_prev.py`.
Result on `B/ota/result`.
