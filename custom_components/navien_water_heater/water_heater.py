"""Water heater platform for the Navien NaviLink integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.water_heater import (
    STATE_GAS,
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, STATE_OFF, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import NavienEntity
from .navien_api import NavienAccount, NavienError

OPERATION_LIST = [STATE_OFF, STATE_GAS]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up one water heater entity per channel of every gateway."""
    account: NavienAccount = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        NavienWaterHeaterEntity(channel) for channel in account.channels
    )


class NavienWaterHeaterEntity(NavienEntity, WaterHeaterEntity):
    """A Navien water heater channel."""

    _attr_name = None
    _attr_operation_list = OPERATION_LIST
    _attr_supported_features = (
        WaterHeaterEntityFeature.TARGET_TEMPERATURE
        | WaterHeaterEntityFeature.OPERATION_MODE
        | WaterHeaterEntityFeature.AWAY_MODE
        | WaterHeaterEntityFeature.ON_OFF
    )

    def __init__(self, channel) -> None:
        """Create the main entity for a channel."""
        super().__init__(channel, "")

    @property
    def temperature_unit(self) -> str:
        """Return the unit the gateway itself reports temperatures in."""
        return (
            UnitOfTemperature.CELSIUS
            if self.channel.is_celsius
            else UnitOfTemperature.FAHRENHEIT
        )

    @property
    def current_temperature(self) -> float | None:
        """Return the outlet temperature."""
        return self.channel.current_temperature

    @property
    def target_temperature(self) -> float | None:
        """Return the domestic hot water setpoint."""
        return self.channel.target_temperature

    @property
    def target_temperature_step(self) -> float:
        """Return the smallest setpoint change the gateway accepts."""
        return self.channel.temperature_step

    @property
    def min_temp(self) -> float:
        """Return the lowest setpoint the gateway accepts."""
        return self.channel.min_temperature

    @property
    def max_temp(self) -> float:
        """Return the highest setpoint the gateway accepts."""
        return self.channel.max_temperature

    @property
    def current_operation(self) -> str:
        """Return whether the burner is enabled."""
        return STATE_GAS if self.channel.power else STATE_OFF

    @property
    def is_away_mode_on(self) -> bool:
        """Away mode is how the NaviLink app models 'heater switched off'."""
        return not self.channel.power

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the handful of values that are useful on a card."""
        status = self.channel.channel_status
        attributes: dict[str, Any] = {
            "mac_address": self.channel.device.mac_address,
            "channel_number": self.channel.channel_number,
            "unit_count": self.channel.unit_count,
        }
        if (value := status.get("avgInletTemp")) is not None:
            attributes["inlet_temperature"] = value
        if (value := self.channel.flow_rate) is not None:
            attributes["flow_rate_lpm"] = value
        if self.channel.supports_on_demand:
            attributes["recirculation"] = self.channel.on_demand
        if (value := status.get("errorCode")) is not None:
            attributes["error_code"] = value
        return attributes

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the domestic hot water setpoint.

        Home Assistant hands the value over already converted to this entity's
        ``temperature_unit``, which is the unit the gateway speaks, so no unit
        juggling is needed here.
        """
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return
        await self._async_command(
            self.channel.async_set_target_temperature(float(temperature))
        )

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        """Turn the burner on or off."""
        if operation_mode not in OPERATION_LIST:
            raise HomeAssistantError(f"Unsupported operation mode: {operation_mode}")
        await self._async_command(
            self.channel.async_set_power(operation_mode == STATE_GAS)
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the water heater on."""
        await self._async_command(self.channel.async_set_power(True))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the water heater off."""
        await self._async_command(self.channel.async_set_power(False))

    async def async_turn_away_mode_on(self) -> None:
        """Turn the water heater off."""
        await self._async_command(self.channel.async_set_power(False))

    async def async_turn_away_mode_off(self) -> None:
        """Turn the water heater on."""
        await self._async_command(self.channel.async_set_power(True))

    async def _async_command(self, coroutine) -> None:
        """Run a command and turn client errors into Home Assistant errors."""
        try:
            await coroutine
        except NavienError as err:
            raise HomeAssistantError(
                f"NaviLink command for {self.channel.name} failed: {err}"
            ) from err
