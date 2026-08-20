"""Button platform for the Navien NaviLink integration."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import NavienDescribedEntity
from .navien_api import NavienAccount, NavienError

REFRESH = ButtonEntityDescription(
    key="refresh",
    translation_key="refresh",
    name="Refresh",
    icon="mdi:refresh",
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up a manual refresh button per channel."""
    account: NavienAccount = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        NavienRefreshButton(channel, REFRESH) for channel in account.channels
    )


class NavienRefreshButton(NavienDescribedEntity, ButtonEntity):
    """Ask a channel for a fresh status without waiting for the next poll."""

    @property
    def available(self) -> bool:
        """The button works as soon as the link is up, even without data yet."""
        return self.channel.device.account.connected and self.channel.device.present

    async def async_press(self) -> None:
        """Request an immediate status update."""
        try:
            await self.channel.async_refresh()
        except NavienError as err:
            raise HomeAssistantError(
                f"NaviLink refresh for {self.channel.name} failed: {err}"
            ) from err
