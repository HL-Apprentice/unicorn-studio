# unicorn CLI

Single-file Python 3 (stdlib only) controller for boards running this firmware. Run `unicorn help`.

```
unicorn status
unicorn mode playlist:evening
unicorn show alien_head --fill          # name from your library, or a path to display JSON
unicorn playlist set evening custom:alien_head=10 clock=10 agenda=10 today=10
unicorn playlist play evening
```
Config: `~/.config/unicorn/config.env` (template `config.env.example`, `chmod 600`), or `$UNICORN_CONFIG`.
Credentials are never taken from the command line, so they do not land in shell history.
