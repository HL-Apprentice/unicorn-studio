# Copy to secrets.py on the board (never commit secrets.py). Only WIFI_* and MQTT_BROKER are required.
WIFI_SSID = "your-wifi-name"
WIFI_PASSWORD = "your-wifi-password"
WIFI_COUNTRY = "US"            # regulatory domain

MQTT_BROKER = "homeassistant.local"   # host name or LAN address of your MQTT broker
MQTT_PORT = 1883
MQTT_USER = "unicorn"          # a dedicated, non-admin broker user (see SECURITY.md for an ACL)
MQTT_PASSWORD = "change-me"

TZ_OFFSET_HOURS = 0            # fallback UTC offset; the HA agenda message carries the live offset (DST)
# Optional:
# HOSTNAME = "cosmic-unicorn"
# TIME_HTTP_HOST = "homeassistant.local"   # time is read from this server's HTTP Date header (default: MQTT_BROKER)
# TIME_HTTP_PORT = 8123
# NTP_HOST = "192.0.2.1"       # e.g. your router; unset = no internet NTP at all
# OTA_KEY = "long-random-string"   # enables signed Wi-Fi file updates; unset = USB updates only
