# WLED

Not supported, on purpose. The Unicorn boards use Pimoroni's own PIO-driven matrix (not WS2812 data lines),
so stock WLED cannot drive them, and a port is a large project. If you already run WLED elsewhere, Home
Assistant can sync both: for example an automation that sets the Unicorn `select.*_mode` and a WLED preset
together. Nothing in this repository depends on WLED.
