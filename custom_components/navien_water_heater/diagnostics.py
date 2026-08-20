"""Diagnostics support for the Navien NaviLink integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_PASSWORD, CONF_USERNAME, DOMAIN
from .navien_api import NavienAccount

TO_REDACT = {
    CONF_PASSWORD,
    CONF_USERNAME,
    "accessKeyId",
    "accessToken",
    "additionalValue",
    "email",
    "homeSeq",
    "macAddress",
    "refreshToken",
    "secretKey",
    "sessionToken",
    "userId",
    "userSeq",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return everything needed to debug an account, minus the secrets."""
    account: NavienAccount | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)

    diagnostics: dict[str, Any] = {
        "entry": {
            "version": entry.version,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "connected": account.connected if account else False,
        "devices": [],
    }

    if account is None:
        return diagnostics

    for device in account.devices.values():
        diagnostics["devices"].append(
            {
                "name": device.name,
                "present": device.present,
                "device_type": device.device_type,
                "info": async_redact_data(dict(device.info), TO_REDACT),
                "channels": [
                    {
                        "channel_number": channel.channel_number,
                        "model": channel.model,
                        "unit_count": channel.unit_count,
                        "temperature_type": channel.temperature_type,
                        "supports_on_demand": channel.supports_on_demand,
                        "last_update": channel.last_update,
                        "channel_info": async_redact_data(
                            dict(channel.channel_info), TO_REDACT
                        ),
                        "channel_status": async_redact_data(
                            dict(channel.channel_status), TO_REDACT
                        ),
                    }
                    for channel in device.channels.values()
                ],
            }
        )

    return diagnostics
