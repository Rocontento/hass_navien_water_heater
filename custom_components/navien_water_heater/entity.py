"""Shared entity plumbing for the Navien NaviLink integration."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity, EntityDescription

from .const import DOMAIN, MANUFACTURER
from .navien_api import NavienChannel


class NavienEntity(Entity):
    """Base class for everything this integration exposes.

    Entities are push driven: the NaviLink client notifies the channel and the
    channel notifies its listeners, so Home Assistant never has to poll us.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, channel: NavienChannel, key: str) -> None:
        """Bind an entity to one channel of one gateway."""
        self.channel = channel
        self._key = key
        self._attr_unique_id = f"{channel.device.mac_address}{channel.channel_number}{key}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return the Home Assistant device this entity belongs to."""
        device = self.channel.device
        return DeviceInfo(
            identifiers={(DOMAIN, f"{device.mac_address}_{self.channel.channel_number}")},
            manufacturer=MANUFACTURER,
            model=self.channel.model,
            name=self.channel.name,
            serial_number=device.mac_address,
            sw_version=device.sw_version,
        )

    @property
    def available(self) -> bool:
        """Return True while the gateway is reachable and has reported data."""
        return self.channel.available

    async def async_added_to_hass(self) -> None:
        """Subscribe to updates for this channel."""
        self.async_on_remove(self.channel.add_listener(self._handle_update))

    def _handle_update(self) -> None:
        """Write the new state after the channel changed."""
        self.async_write_ha_state()


class NavienDescribedEntity(NavienEntity):
    """Base class for entities driven by an EntityDescription."""

    def __init__(self, channel: NavienChannel, description: EntityDescription) -> None:
        """Bind a described entity to a channel."""
        super().__init__(channel, description.key)
        self.entity_description = description


class NavienUnitEntity(NavienDescribedEntity):
    """Base class for entities that describe one physical unit of a channel.

    Cascade installations put several heaters behind a single channel; each of
    them reports its own temperatures, flow and gas usage.
    """

    def __init__(
        self,
        channel: NavienChannel,
        description: EntityDescription,
        unit_number: int,
    ) -> None:
        """Bind an entity to one unit of a cascade channel."""
        super().__init__(channel, description)
        self.unit_number = unit_number
        self._attr_unique_id = (
            f"{channel.device.mac_address}{channel.channel_number}"
            f"{unit_number}{description.key}"
        )
        if channel.unit_count > 1:
            # Cascade system: disambiguate the units.  An explicit name wins
            # over the translated one, which is the right trade-off here.
            self._attr_name = f"{description.name} unit {unit_number}"

    @property
    def unit_status(self) -> dict[str, Any]:
        """Return the status block of the unit this entity belongs to."""
        for unit in self.channel.units:
            if unit.get("unitNumber") == self.unit_number:
                return unit
        return {}
