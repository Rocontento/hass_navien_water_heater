"""Constants for the Navien NaviLink Water Heater integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "navien_water_heater"

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.WATER_HEATER,
]

MANUFACTURER = "Navien"

# --- Config entry keys -------------------------------------------------------
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_POLLING_INTERVAL = "polling_interval"

# Legacy key kept only so migrations can recognise/clean up v1 entries.
CONF_DEVICE_INDEX = "device_index"

DEFAULT_POLLING_INTERVAL = 30
MIN_POLLING_INTERVAL = 10
MAX_POLLING_INTERVAL = 600

# --- Connection tuning -------------------------------------------------------
NAVIEN_API_BASE = "https://nlus.naviensmartcontrol.com/api/v2"
AWS_IOT_ENDPOINT = "a1t30mldyslmuq-ats.iot.us-east-1.amazonaws.com"
AWS_IOT_PORT = 443
AWS_CERT_FILE = "AmazonRootCA1.pem"

# The MQTT credentials handed out by the REST API are temporary AWS session
# credentials.  Re-authenticate well before they can expire instead of the
# old "once a day at 02:00" behaviour.
CREDENTIAL_REFRESH_SECONDS = 6 * 60 * 60

# How long to wait for a response to a request/response style MQTT round trip.
RESPONSE_TIMEOUT = 12
# How long the initial connect (login + mqtt + first status) may take.
SETUP_TIMEOUT = 90

RECONNECT_INITIAL_DELAY = 5
RECONNECT_MAX_DELAY = 300

# Small delay between consecutive status requests so a large account does not
# fire every request in the same millisecond.
REQUEST_SPACING = 0.15

# --- Protocol commands -------------------------------------------------------
CMD_CHANNEL_INFO = 16777217
CMD_CHANNEL_STATUS = 16777220
CMD_CONTROL_POWER = 33554433
CMD_CONTROL_TEMPERATURE = 33554435
CMD_CONTROL_ON_DEMAND = 33554437

STATE_ON = 1
STATE_OFF_VALUE = 2

# --- Unit conversion ---------------------------------------------------------
# 1 kcal/h expressed in watts.
KCAL_PER_HOUR_TO_WATT = 1.163

# Device families whose instantaneous gas usage is reported with an extra
# factor of ten compared to the rest of the line-up.
HIGH_RESOLUTION_GAS_TYPES = {6, 8, 13, 14}  # NFB, NFC, NCB_H, NVW

# Fallback DHW limits (in the unit reported by the device) used when the
# gateway does not report usable limits.
FALLBACK_TEMP_LIMITS = {
    "celsius": (35.0, 60.0),
    "fahrenheit": (95.0, 140.0),
}
