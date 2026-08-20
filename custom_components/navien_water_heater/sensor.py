"""Sensor platform for the Navien NaviLink integration."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.components.sensor.const import UNIT_CONVERTERS
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import DOMAIN
from .entity import NavienDescribedEntity, NavienUnitEntity
from .navien_api import NavienAccount, NavienChannel

REVOLUTIONS_PER_MINUTE = "rpm"


@dataclass(frozen=True, kw_only=True)
class NavienSensorEntityDescription(SensorEntityDescription):
    """Describes a sensor built from the channel status."""

    value_fn: Callable[[NavienChannel], StateType]
    supported_fn: Callable[[NavienChannel], bool] = lambda _channel: True
    imperial_unit: str | None = None


@dataclass(frozen=True, kw_only=True)
class NavienUnitSensorEntityDescription(SensorEntityDescription):
    """Describes a sensor built from a single unit's status."""

    value_fn: Callable[[dict[str, Any]], StateType] = lambda unit: None
    imperial_unit: str | None = None


def _status(channel: NavienChannel, key: str) -> StateType:
    """Read a value straight out of the channel status."""
    value = channel.channel_status.get(key)
    return value if isinstance(value, (int, float, str)) else None


def _plain(key: str) -> Callable[[dict[str, Any]], StateType]:
    """Read a value straight out of a unit status."""

    def _value(unit: dict[str, Any]) -> StateType:
        value = unit.get(key)
        return value if isinstance(value, (int, float, str)) else None

    return _value


def _has(key: str) -> Callable[[NavienChannel], bool]:
    """Only build the entity when the gateway actually reports the key."""
    return lambda channel: key in channel.channel_status


# --------------------------------------------------------------------------- #
# Channel level sensors
# --------------------------------------------------------------------------- #
CHANNEL_SENSORS: tuple[NavienSensorEntityDescription, ...] = (
    NavienSensorEntityDescription(
        key="avgCalorie",
        translation_key="heating_capacity",
        name="Heating capacity",
        icon="mdi:fire",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda channel: _status(channel, "avgCalorie"),
        supported_fn=_has("avgCalorie"),
    ),
    NavienSensorEntityDescription(
        key="DHWSettingTemp",
        translation_key="target_temperature",
        name="Target temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda channel: channel.target_temperature,
        supported_fn=_has("DHWSettingTemp"),
    ),
    NavienSensorEntityDescription(
        key="avgInletTemp",
        translation_key="average_inlet_temperature",
        name="Average inlet temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda channel: _status(channel, "avgInletTemp"),
        supported_fn=lambda channel: "avgInletTemp" in channel.channel_status
        and channel.unit_count > 1,
    ),
    NavienSensorEntityDescription(
        key="avgOutletTemp",
        translation_key="average_outlet_temperature",
        name="Average outlet temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda channel: _status(channel, "avgOutletTemp"),
        supported_fn=lambda channel: "avgOutletTemp" in channel.channel_status
        and channel.unit_count > 1,
    ),
    # Cascade totals.  With a single unit these would just duplicate the unit
    # sensors, so they are only created when there is something to add up.
    NavienSensorEntityDescription(
        key="channelFlowRate",
        translation_key="total_flow_rate",
        name="Total hot water flow",
        icon="mdi:water",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        native_unit_of_measurement=UnitOfVolumeFlowRate.LITERS_PER_MINUTE,
        imperial_unit=UnitOfVolumeFlowRate.GALLONS_PER_MINUTE,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda channel: channel.flow_rate,
        supported_fn=lambda channel: channel.unit_count > 1,
    ),
    NavienSensorEntityDescription(
        key="channelGasInstantUsage",
        translation_key="total_burner_output",
        name="Total burner output",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda channel: channel.gas_instant_usage,
        supported_fn=lambda channel: channel.unit_count > 1,
    ),
    NavienSensorEntityDescription(
        key="channelAccumulatedGasUsage",
        translation_key="total_gas_used",
        name="Total gas used",
        device_class=SensorDeviceClass.GAS,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        imperial_unit=UnitOfVolume.CUBIC_FEET,
        suggested_display_precision=1,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda channel: channel.accumulated_gas_usage,
        supported_fn=lambda channel: channel.unit_count > 1,
    ),
    NavienSensorEntityDescription(
        key="errorCode",
        translation_key="error_code",
        name="Error code",
        icon="mdi:alert-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda channel: _status(channel, "errorCode"),
        supported_fn=_has("errorCode"),
    ),
    NavienSensorEntityDescription(
        key="subErrorCode",
        translation_key="sub_error_code",
        name="Sub error code",
        icon="mdi:alert-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda channel: _status(channel, "subErrorCode"),
        supported_fn=_has("subErrorCode"),
    ),
    NavienSensorEntityDescription(
        key="lastUpdate",
        translation_key="last_update",
        name="Last update",
        icon="mdi:clock-outline",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda channel: (
            datetime.fromtimestamp(channel.last_update, tz=timezone.utc)
            if channel.last_update
            else None
        ),
    ),
)


# --------------------------------------------------------------------------- #
# Per unit sensors
# --------------------------------------------------------------------------- #
UNIT_SENSORS: tuple[NavienUnitSensorEntityDescription, ...] = (
    NavienUnitSensorEntityDescription(
        key="currentOutletTemp",
        translation_key="outlet_temperature",
        name="Hot water temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_plain("currentOutletTemp"),
    ),
    NavienUnitSensorEntityDescription(
        key="currentInletTemp",
        translation_key="inlet_temperature",
        name="Inlet temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_plain("currentInletTemp"),
    ),
    NavienUnitSensorEntityDescription(
        key="DHWFlowRate",
        translation_key="flow_rate",
        name="Hot water flow",
        icon="mdi:water",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        native_unit_of_measurement=UnitOfVolumeFlowRate.LITERS_PER_MINUTE,
        imperial_unit=UnitOfVolumeFlowRate.GALLONS_PER_MINUTE,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_plain("DHWFlowRate"),
    ),
    NavienUnitSensorEntityDescription(
        key="gasInstantUsage",
        translation_key="burner_output",
        name="Burner output",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_plain("gasInstantUsage"),
    ),
    NavienUnitSensorEntityDescription(
        key="accumulatedGasUsage",
        translation_key="gas_used",
        name="Gas used",
        device_class=SensorDeviceClass.GAS,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        imperial_unit=UnitOfVolume.CUBIC_FEET,
        suggested_display_precision=1,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=_plain("accumulatedGasUsage"),
    ),
    NavienUnitSensorEntityDescription(
        key="currentInnerTemp",
        translation_key="inner_temperature",
        name="Internal temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_plain("currentInnerTemp"),
    ),
    NavienUnitSensorEntityDescription(
        key="errorCode",
        translation_key="error_code",
        name="Error code",
        icon="mdi:alert-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_plain("errorCode"),
    ),
    NavienUnitSensorEntityDescription(
        key="subErrorCode",
        translation_key="sub_error_code",
        name="Sub error code",
        icon="mdi:alert-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_plain("subErrorCode"),
    ),
    NavienUnitSensorEntityDescription(
        key="totalOperatedTime",
        translation_key="total_operated_time",
        name="Total operating time",
        icon="mdi:timer-outline",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_plain("totalOperatedTime"),
    ),
    NavienUnitSensorEntityDescription(
        key="fanRPM",
        translation_key="fan_speed",
        name="Fan speed",
        icon="mdi:fan",
        native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_plain("fanRPM"),
    ),
)

# Keys handled elsewhere (binary sensors, structural fields, curated sensors)
# and therefore skipped by the generic fallback below.
_HANDLED_CHANNEL_KEYS = {
    "avgCalorie",
    "avgInletTemp",
    "avgOutletTemp",
    "channelNumber",
    "DHWSettingTemp",
    "DHWUse",
    "errorCode",
    "onDemandUseFlag",
    "powerStatus",
    "subErrorCode",
    "unitCount",
    "unitInfo",
    "unitType",
    "wwsdFlag",
}

_HANDLED_UNIT_KEYS = {description.key for description in UNIT_SENSORS} | {
    "DHWUse",
    "powerStatus",
    "unitNumber",
}


def _is_plain_number(value: Any) -> bool:
    """Return True for a numeric value that is not one of the on/off flags.

    ``bool`` is a subclass of ``int`` in Python, so flags the client already
    turned into booleans would otherwise show up as generic number sensors.
    """
    return not isinstance(value, bool) and isinstance(value, (int, float))


def _humanize(key: str) -> str:
    """Turn a camelCase payload key into a sentence cased entity name."""
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", key)
    text = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", text)
    words = text.split()
    if not words:
        return key
    result = []
    for index, word in enumerate(words):
        if word.isupper():
            result.append(word)  # keep acronyms such as DHW or RPM intact
        elif index == 0:
            result.append(word[:1].upper() + word[1:])
        else:
            result.append(word[:1].lower() + word[1:])
    return " ".join(result)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the Navien sensors."""
    account: NavienAccount = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []

    for channel in account.channels:
        for description in CHANNEL_SENSORS:
            if description.supported_fn(channel):
                entities.append(NavienChannelSensor(channel, description))

        for unit in channel.units:
            unit_number = unit.get("unitNumber")
            if unit_number is None:
                continue
            for unit_description in UNIT_SENSORS:
                if unit_description.key not in unit:
                    continue
                if (
                    unit_description.key in ("errorCode", "subErrorCode")
                    and channel.unit_count == 1
                    and unit_description.key in channel.channel_status
                ):
                    # Already covered by the channel wide sensor.
                    continue
                entities.append(
                    NavienUnitSensor(channel, unit_description, unit_number)
                )

            # Anything the gateway reports that we do not have a curated
            # description for still gets an entity, disabled by default, so
            # nothing the NaviLink app knows about stays out of reach.
            for key, value in unit.items():
                if key in _HANDLED_UNIT_KEYS or not _is_plain_number(value):
                    continue
                entities.append(
                    NavienUnitSensor(
                        channel,
                        NavienUnitSensorEntityDescription(
                            key=key,
                            name=_humanize(key),
                            entity_category=EntityCategory.DIAGNOSTIC,
                            entity_registry_enabled_default=False,
                            value_fn=_plain(key),
                        ),
                        unit_number,
                    )
                )

        for key, value in channel.channel_status.items():
            if key in _HANDLED_CHANNEL_KEYS or not _is_plain_number(value):
                continue
            entities.append(
                NavienChannelSensor(
                    channel,
                    NavienSensorEntityDescription(
                        key=key,
                        name=_humanize(key),
                        entity_category=EntityCategory.DIAGNOSTIC,
                        entity_registry_enabled_default=False,
                        value_fn=lambda channel, key=key: _status(channel, key),
                    ),
                )
            )

    async_add_entities(entities)


class NavienSensorMixin:
    """Unit handling shared by the channel and per unit sensors."""

    def _apply_units(self, channel: NavienChannel, description: Any) -> None:
        """Pick the native unit and the unit the user most likely wants."""
        if description.device_class == SensorDeviceClass.TEMPERATURE:
            self._attr_native_unit_of_measurement = (
                UnitOfTemperature.CELSIUS
                if channel.is_celsius
                else UnitOfTemperature.FAHRENHEIT
            )
        elif getattr(description, "imperial_unit", None) and not channel.is_celsius:
            # The gateway is configured for imperial units, so present the
            # values that way by default and let Home Assistant convert.  Only
            # suggest a unit it actually knows how to convert to, otherwise it
            # refuses to add the entity at all.
            converter = UNIT_CONVERTERS.get(description.device_class)
            if converter is not None and description.imperial_unit in converter.VALID_UNITS:
                self._attr_suggested_unit_of_measurement = description.imperial_unit


class NavienChannelSensor(NavienSensorMixin, NavienDescribedEntity, SensorEntity):
    """A sensor built from the status of a whole channel."""

    entity_description: NavienSensorEntityDescription

    def __init__(
        self, channel: NavienChannel, description: NavienSensorEntityDescription
    ) -> None:
        """Create the sensor and pin down its units."""
        super().__init__(channel, description)
        self._apply_units(channel, description)

    @property
    def native_value(self) -> StateType | datetime:
        """Return the current value of this sensor."""
        return self.entity_description.value_fn(self.channel)


class NavienUnitSensor(NavienSensorMixin, NavienUnitEntity, SensorEntity):
    """A sensor built from the status of one physical unit."""

    entity_description: NavienUnitSensorEntityDescription

    def __init__(
        self,
        channel: NavienChannel,
        description: NavienUnitSensorEntityDescription,
        unit_number: int,
    ) -> None:
        """Create the sensor and pin down its units."""
        super().__init__(channel, description, unit_number)
        self._apply_units(channel, description)

    @property
    def native_value(self) -> StateType:
        """Return the current value of this sensor."""
        return self.entity_description.value_fn(self.unit_status)
