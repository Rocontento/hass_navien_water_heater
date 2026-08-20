"""Number platform for the Navien NaviLink integration."""

from __future__ import annotations

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import NavienDescribedEntity
from .navien_api import NavienAccount, NavienChannel, NavienError

TARGET_TEMPERATURE = NumberEntityDescription(
    key="target_temperature",
    translation_key="target_temperature",
    name="Target temperature",
    icon="mdi:thermometer-water",
    device_class=NumberDeviceClass.TEMPERATURE,
    mode=NumberMode.SLIDER,
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up a setpoint slider per channel."""
    account: NavienAccount = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        NavienTargetTemperature(channel, TARGET_TEMPERATURE)
        for channel in account.channels
    )


class NavienTargetTemperature(NavienDescribedEntity, NumberEntity):
    """The domestic hot water setpoint as a plain slider."""

    def __init__(self, channel: NavienChannel, description) -> None:
        """Create the slider in the unit the gateway speaks."""
        super().__init__(channel, description)
        self._attr_native_unit_of_measurement = (
            UnitOfTemperature.CELSIUS
            if channel.is_celsius
            else UnitOfTemperature.FAHRENHEIT
        )

    @property
    def native_min_value(self) -> float:
        """Return the lowest setpoint the gateway accepts."""
        return self.channel.min_temperature

    @property
    def native_max_value(self) -> float:
        """Return the highest setpoint the gateway accepts."""
        return self.channel.max_temperature

    @property
    def native_step(self) -> float:
        """Return the smallest setpoint change the gateway accepts."""
        return self.channel.temperature_step

    @property
    def native_value(self) -> float | None:
        """Return the current setpoint."""
        return self.channel.target_temperature

    async def async_set_native_value(self, value: float) -> None:
        """Send a new setpoint to the gateway."""
        try:
            await self.channel.async_set_target_temperature(value)
        except NavienError as err:
            raise HomeAssistantError(
                f"NaviLink command for {self.channel.name} failed: {err}"
            ) from err
