"""Switch platform for the Navien NaviLink integration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Coroutine

from homeassistant.components.switch import (
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import NavienDescribedEntity
from .navien_api import NavienAccount, NavienChannel, NavienError


@dataclass(frozen=True, kw_only=True)
class NavienSwitchEntityDescription(SwitchEntityDescription):
    """Describes a Navien switch."""

    value_fn: Callable[[NavienChannel], bool]
    set_fn: Callable[[NavienChannel, bool], Coroutine[Any, Any, None]]
    supported_fn: Callable[[NavienChannel], bool] = lambda _channel: True


SWITCHES: tuple[NavienSwitchEntityDescription, ...] = (
    NavienSwitchEntityDescription(
        key="power_button",
        translation_key="power",
        name="Power",
        icon="mdi:power",
        value_fn=lambda channel: channel.power,
        set_fn=lambda channel, state: channel.async_set_power(state),
    ),
    NavienSwitchEntityDescription(
        key="hot_button",
        translation_key="recirculation",
        name="Recirculation",
        icon="mdi:pump",
        value_fn=lambda channel: channel.on_demand,
        set_fn=lambda channel, state: channel.async_set_on_demand(state),
        supported_fn=lambda channel: channel.supports_on_demand,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Navien switches."""
    account: NavienAccount = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        NavienSwitch(channel, description)
        for channel in account.channels
        for description in SWITCHES
        if description.supported_fn(channel)
    )


class NavienSwitch(NavienDescribedEntity, SwitchEntity):
    """A switchable function of a Navien water heater channel."""

    entity_description: NavienSwitchEntityDescription

    @property
    def is_on(self) -> bool:
        """Return the current state of this function."""
        return self.entity_description.value_fn(self.channel)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn this function on."""
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn this function off."""
        await self._async_set(False)

    async def _async_set(self, state: bool) -> None:
        """Send the command and report failures to the user."""
        try:
            await self.entity_description.set_fn(self.channel, state)
        except NavienError as err:
            raise HomeAssistantError(
                f"NaviLink command for {self.channel.name} failed: {err}"
            ) from err
