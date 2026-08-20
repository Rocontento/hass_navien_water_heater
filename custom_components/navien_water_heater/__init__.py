"""The Navien NaviLink Water Heater integration."""

from __future__ import annotations

import asyncio
import logging
import os

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    AWS_CERT_FILE,
    CONF_DEVICE_INDEX,
    CONF_PASSWORD,
    CONF_POLLING_INTERVAL,
    CONF_USERNAME,
    DEFAULT_POLLING_INTERVAL,
    DOMAIN,
    PLATFORMS,
    SETUP_TIMEOUT,
)
from .navien_api import (
    NavienAccount,
    NavienAuthError,
    NavienError,
    NavienNoDevicesError,
)

_LOGGER = logging.getLogger(__name__)


def _polling_interval(entry: ConfigEntry) -> int:
    """Return the polling interval configured for an entry."""
    return int(
        entry.options.get(
            CONF_POLLING_INTERVAL,
            entry.data.get(CONF_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL),
        )
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a NaviLink account from a config entry."""
    account = NavienAccount(
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        session=async_get_clientsession(hass),
        aws_cert_path=os.path.join(os.path.dirname(__file__), "cert", AWS_CERT_FILE),
        polling_interval=_polling_interval(entry),
    )
    account.set_auth_failure_callback(lambda: entry.async_start_reauth(hass))

    try:
        async with asyncio.timeout(SETUP_TIMEOUT):
            await account.async_setup()
    except NavienAuthError as err:
        await account.async_stop()
        raise ConfigEntryAuthFailed(str(err)) from err
    except NavienNoDevicesError as err:
        await account.async_stop()
        raise ConfigEntryNotReady(str(err)) from err
    except (NavienError, TimeoutError) as err:
        await account.async_stop()
        raise ConfigEntryNotReady(f"Unable to reach the NaviLink cloud: {err}") from err
    except Exception:
        await account.async_stop()
        raise

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = account
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply changed options without tearing the connection down when possible."""
    account: NavienAccount | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if account is None:
        return
    account.set_polling_interval(_polling_interval(entry))


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    account: NavienAccount | None = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if account is not None:
        await account.async_stop()
    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow removing a device that the account no longer contains."""
    account: NavienAccount | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if account is None:
        return True
    known = {
        f"{channel.device.mac_address}_{channel.channel_number}"
        for channel in account.channels
    }
    return not any(
        identifier[1] in known
        for identifier in device_entry.identifiers
        if identifier[0] == DOMAIN
    )


# --------------------------------------------------------------------------- #
# Migration
# --------------------------------------------------------------------------- #
async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate an old config entry to the account wide layout.

    Version 1 stored one entry per water heater and identified that heater by
    its *position* in the NaviLink device list, which silently started pointing
    at the wrong heater as soon as another one was added to the account.
    Version 2 stores the account once and discovers every gateway by MAC.
    """
    if entry.version >= 2:
        return True

    username = entry.data.get(CONF_USERNAME, "")
    unique_id = username.lower()

    siblings = [
        candidate
        for candidate in hass.config_entries.async_entries(DOMAIN)
        if (candidate.data.get(CONF_USERNAME) or "").lower() == unique_id
    ]
    # Deterministic winner so concurrent migrations cannot disagree.
    keeper = min(siblings, key=lambda candidate: candidate.entry_id)

    if keeper.entry_id != entry.entry_id:
        _LOGGER.info(
            "A single NaviLink entry now covers every water heater on account %s; "
            "folding the extra entry into %s",
            username,
            keeper.title,
        )
        _reassign_registry_entries(hass, entry.entry_id, keeper.entry_id)
        hass.async_create_task(hass.config_entries.async_remove(entry.entry_id))
        return False

    data = {CONF_USERNAME: username, CONF_PASSWORD: entry.data.get(CONF_PASSWORD, "")}
    options = {
        CONF_POLLING_INTERVAL: int(
            entry.data.get(CONF_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL)
        )
    }
    if CONF_DEVICE_INDEX in entry.data:
        _LOGGER.info(
            "NaviLink water heaters are now discovered by MAC address instead of by "
            "their position in the account, so every heater on %s is set up at once",
            username,
        )

    hass.config_entries.async_update_entry(
        entry,
        data=data,
        options=options,
        title=username or entry.title,
        unique_id=unique_id or None,
        version=2,
    )
    return True


def _reassign_registry_entries(
    hass: HomeAssistant, from_entry_id: str, to_entry_id: str
) -> None:
    """Hand devices and entities of a dropped entry over to the surviving one.

    Without this the registry would drop them when the duplicate entry is
    removed, and the user would lose entity ids, names and area assignments.
    """
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    for device in dr.async_entries_for_config_entry(device_registry, from_entry_id):
        try:
            device_registry.async_update_device(
                device.id, add_config_entry_id=to_entry_id
            )
        except (ValueError, KeyError) as err:  # pragma: no cover - defensive
            _LOGGER.debug("Could not move device %s: %s", device.id, err)

    for entity in er.async_entries_for_config_entry(entity_registry, from_entry_id):
        try:
            entity_registry.async_update_entity(
                entity.entity_id, config_entry_id=to_entry_id
            )
        except (ValueError, KeyError) as err:  # pragma: no cover - defensive
            _LOGGER.debug("Could not move entity %s: %s", entity.entity_id, err)
