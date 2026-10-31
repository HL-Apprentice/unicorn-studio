# Home Assistant

## The board (no YAML needed)
With the MQTT integration set up, the board announces itself by MQTT discovery as one device with:

| Entity | Notes |
|---|---|
| `light.unicorn_<id>` | on/off, brightness, colour, effect list = all modes |
| `select.unicorn_<id>_mode` | every built-in mode, `custom:<name>` for each uploaded display, **`playlist:<name>` for each playlist** (the list updates when you add/delete displays or playlists) |
| `select.unicorn_<id>_rotation` | 0/90/180/270 (Galactic: 0/180) |
| `text.unicorn_<id>_text` | scroll text |
| `number.unicorn_<id>_speed` | 1-20 |
| `button.unicorn_<id>_reboot` | |
| `sensor.unicorn_<id>_playing` | current playlist item |
| `sensor.unicorn_<id>_ip`, `sensor.unicorn_<id>_uptime` | diagnostics (the IP is only published to your broker) |

`<id>` = last 6 hex digits of the board's MAC. Example automation:
```yaml
- alias: "Unicorn evening playlist"
  triggers: [{trigger: time, at: "18:00:00"}]
  actions:
    - action: select.select_option
      target: {entity_id: select.unicorn_abc123_mode}
      data: {option: "playlist:evening"}
```

## Agenda (optional)
`unicorn_agenda.yaml` is a package that reads up to three calendars every 5 minutes with
`calendar.get_events` and publishes a retained `unicorn/agenda` message (also carrying the current UTC
offset, so the board's clock follows DST). Edit the two `calendar.change_me_*` ids, then restart HA.
Because the message is retained, the broker keeps your event titles; anyone allowed to read `unicorn/#` can
see them (see `SECURITY.md`).

## Broker user
Give the board its own non-admin MQTT user; see `SECURITY.md` for a Mosquitto ACL that limits it to
`unicorn/#` and to its own discovery topics (`homeassistant/<component>/unicorn_<id>/<entity>/config`, us-2.2).
Flash us-2.2 before tightening the ACL so the board can clear its old discovery topics.
