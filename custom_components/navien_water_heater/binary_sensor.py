"""Binary sensor platform for the Navien NaviLink integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import NavienDescribedEntity
from .navien_api import NavienAccount, NavienChannel


@dataclass(frozen=True, kw_only=True)
class NavienBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes a Navien binary sensor."""

    value_fn: Callable[[NavienChannel], bool | None]
    supported_fn: Callable[[NavienChannel], bool] = lambda _channel: True


def _burner_active(channel: NavienChannel) -> bool | None:
    """Return True while the burner is firing."""
    for key in ("avgCalorie",):
        value = channel.channel_status.get(key)
        if isinstance(value, (int, float)):
            return value > 0
    output = channel.gas_instant_usage
    return None if output is None else output > 0


def _hot_water_in_use(channel: NavienChannel) -> bool | None:
    """Return True while hot water is being drawn."""
    for unit in channel.units:
        if unit.get("DHWUse") is True:
            return True
    flow = channel.flow_rate
    if flow is None:
        return None
    return flow > 0


def _problem(channel: NavienChannel) -> bool | None:
    """Return True while the channel or one of its units reports an error."""
    codes = [channel.channel_status.get("errorCode")]
    codes.extend(unit.get("errorCode") for unit in channel.units)
    numeric = [code for code in codes if isinstance(code, (int, float))]
    if not numeric:
        return None
    return any(code != 0 for code in numeric)


BINARY_SENSORS: tuple[NavienBinarySensorEntityDescription, ...] = (
    NavienBinarySensorEntityDescription(
        key="dhwInUse",
        translation_key="hot_water_in_use",
        name="Hot water in use",
        icon="mdi:water-pump",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=_hot_water_in_use,
    ),
    NavienBinarySensorEntityDescription(
        key="burnerActive",
        translation_key="burner",
        name="Burner",
        icon="mdi:fire",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=_burner_active,
    ),
    NavienBinarySensorEntityDescription(
        key="problem",
        translation_key="problem",
        name="Problem",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_problem,
    ),
    NavienBinarySensorEntityDescription(
        key="wwsdFlag",
        translation_key="warm_weather_shutdown",
        name="Warm weather shutdown",
        icon="mdi:weather-sunny",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda channel: bool(channel.channel_status.get("wwsdFlag")),
        supported_fn=lambda channel: "wwsdFlag" in channel.channel_status,
    ),
    NavienBinarySensorEntityDescription(
        key="freezeProtectionUse",
        translation_key="freeze_protection",
        name="Freeze protection",
        icon="mdi:snowflake",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda channel: bool(
            channel.channel_status.get("freezeProtectionUse")
        ),
        supported_fn=lambda channel: "freezeProtectionUse" in channel.channel_status,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Navien binary sensors."""
    account: NavienAccount = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        NavienBinarySensor(channel, description)
        for channel in account.channels
        for description in BINARY_SENSORS
        if description.supported_fn(channel)
    )


class NavienBinarySensor(NavienDescribedEntity, BinarySensorEntity):
    """A derived on/off state of a Navien water heater channel."""

    entity_description: NavienBinarySensorEntityDescription

    @property
    def is_on(self) -> bool | None:
        """Return the current state."""
        return self.entity_description.value_fn(self.channel)
