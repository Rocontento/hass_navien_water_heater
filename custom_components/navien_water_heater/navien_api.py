"""Asynchronous client for the Navien NaviLink cloud service.

The NaviLink cloud is a two stage affair:

* a small REST API used to sign in and to enumerate the gateways ("devices")
  that belong to an account, and
* an AWS IoT MQTT broker used for the actual request/response traffic with
  each gateway.

Compared to the original implementation this module manages an **entire
account** over a **single** MQTT connection.  Every gateway is addressed by
its MAC address instead of by its position in the REST device list, which is
what used to break as soon as a second water heater was added to an account.
"""

from __future__ import annotations

import asyncio
import enum
import json
import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

import AWSIoTPythonSDK.MQTTLib as mqtt
import aiohttp

from .const import (
    AWS_IOT_ENDPOINT,
    AWS_IOT_PORT,
    CMD_CHANNEL_INFO,
    CMD_CHANNEL_STATUS,
    CMD_CONTROL_ON_DEMAND,
    CMD_CONTROL_POWER,
    CMD_CONTROL_TEMPERATURE,
    CREDENTIAL_REFRESH_SECONDS,
    DEFAULT_POLLING_INTERVAL,
    FALLBACK_TEMP_LIMITS,
    HIGH_RESOLUTION_GAS_TYPES,
    KCAL_PER_HOUR_TO_WATT,
    NAVIEN_API_BASE,
    RECONNECT_INITIAL_DELAY,
    RECONNECT_MAX_DELAY,
    REQUEST_SPACING,
    RESPONSE_TIMEOUT,
    STATE_OFF_VALUE,
    STATE_ON,
)

_LOGGER = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class NavienError(Exception):
    """Base error for the NaviLink client."""


class NavienAuthError(NavienError):
    """The supplied credentials were rejected by the NaviLink cloud."""


class NavienConnectionError(NavienError):
    """The NaviLink cloud could not be reached."""


class NavienNoDevicesError(NavienError):
    """The account does not contain any NaviLink gateway."""


class NavienResponseError(NavienError):
    """The NaviLink cloud returned something we could not parse."""


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class DeviceSorting(enum.IntEnum):
    """Navien product families, as numbered by the NaviLink app."""

    NO_DEVICE = 0
    NPE = 1
    NCB = 2
    NHB = 3
    CAS_NPE = 4
    CAS_NHB = 5
    NFB = 6
    CAS_NFB = 7
    NFC = 8
    NPN = 9
    CAS_NPN = 10
    NPE2 = 11
    CAS_NPE2 = 12
    NCB_H = 13
    NVW = 14
    CAS_NVW = 15


MODEL_NAMES = {
    DeviceSorting.NPE: "NPE",
    DeviceSorting.NCB: "NCB",
    DeviceSorting.NHB: "NHB",
    DeviceSorting.CAS_NPE: "NPE (cascade)",
    DeviceSorting.CAS_NHB: "NHB (cascade)",
    DeviceSorting.NFB: "NFB",
    DeviceSorting.CAS_NFB: "NFB (cascade)",
    DeviceSorting.NFC: "NFC",
    DeviceSorting.NPN: "NPN",
    DeviceSorting.CAS_NPN: "NPN (cascade)",
    DeviceSorting.NPE2: "NPE-2",
    DeviceSorting.CAS_NPE2: "NPE-2 (cascade)",
    DeviceSorting.NCB_H: "NCB-H",
    DeviceSorting.NVW: "NVW",
    DeviceSorting.CAS_NVW: "NVW (cascade)",
}


class TemperatureType(enum.IntEnum):
    """Unit system a gateway reports its temperatures in."""

    UNKNOWN = 0
    CELSIUS = 1
    FAHRENHEIT = 2


# --------------------------------------------------------------------------- #
# Topic / message builders
# --------------------------------------------------------------------------- #
class Topics:
    """MQTT topic names for a single gateway."""

    def __init__(self, user_info: dict, device_info: dict, client_id: str) -> None:
        """Build the topic prefixes for one gateway."""
        info = device_info.get("deviceInfo", {})
        self.user_seq = str(user_info.get("userInfo", {}).get("userSeq", ""))
        self.mac_address = info.get("macAddress", "")
        self.home_seq = str(info.get("homeSeq", ""))
        self.device_type = str(info.get("deviceType", ""))
        self.client_id = client_id
        self.req = f"cmd/{self.device_type}/navilink-{self.mac_address}/"
        self.res = f"cmd/{self.device_type}/{self.home_seq}/{self.user_seq}/{self.client_id}/res/"

    def start(self) -> str:
        """Topic used to ask a gateway to describe its channels."""
        return self.req + "status/start"

    def channel_info_sub(self) -> str:
        """Broadcast channel information topic."""
        return self.req + "res/channelinfo"

    def channel_info_res(self) -> str:
        """Channel information response addressed to this client."""
        return self.res + "channelinfo"

    def control_fail(self) -> str:
        """Topic the gateway uses to report a rejected control command."""
        return self.req + "res/controlfail"

    def channel_status_sub(self) -> str:
        """Broadcast channel status topic."""
        return self.req + "res/channelstatus"

    def channel_status_req(self) -> str:
        """Topic used to request a channel status update."""
        return self.req + "status/channelstatus"

    def channel_status_res(self) -> str:
        """Channel status response addressed to this client."""
        return self.res + "channelstatus"

    def control(self) -> str:
        """Topic used to send control commands."""
        return self.req + "control"

    def connection(self) -> str:
        """Gateway connection event topic."""
        return self.req + "connection"

    def app_connection(self) -> str:
        """Topic used for the MQTT last will message."""
        return f"evt/1/navilink-{self.mac_address}/app-connection"


class Messages:
    """MQTT payload builders for a single gateway."""

    def __init__(self, device_info: dict, client_id: str, topics: Topics) -> None:
        """Store the identifiers every payload has to carry."""
        info = device_info.get("deviceInfo", {})
        self.mac_address = info.get("macAddress", "")
        self.device_type = int(info.get("deviceType", 1) or 1)
        self.additional_value = info.get("additionalValue", "")
        self.client_id = client_id
        self.topics = topics

    def _envelope(self, request: dict, request_topic: str, response_topic: str) -> dict:
        return {
            "clientID": self.client_id,
            "protocolVersion": 1,
            "request": request,
            "requestTopic": request_topic,
            "responseTopic": response_topic,
            "sessionID": "",
        }

    def _base_request(self, command: int) -> dict:
        return {
            "additionalValue": self.additional_value,
            "command": command,
            "deviceType": self.device_type,
            "macAddress": self.mac_address,
        }

    def channel_info(self) -> dict:
        """Payload asking the gateway to enumerate its channels."""
        return self._envelope(
            self._base_request(CMD_CHANNEL_INFO),
            self.topics.start(),
            self.topics.channel_info_res(),
        )

    def channel_status(self, channel_number: int, unit_count: int) -> dict:
        """Payload asking for the status of one channel."""
        request = self._base_request(CMD_CHANNEL_STATUS)
        request["status"] = {
            "channelNumber": channel_number,
            "unitNumberEnd": unit_count,
            "unitNumberStart": 1,
        }
        return self._envelope(
            request,
            self.topics.channel_status_req(),
            self.topics.channel_status_res(),
        )

    def _control(self, command: int, mode: str, channel_number: int, param: list) -> dict:
        request = self._base_request(command)
        request["control"] = {
            "channelNumber": channel_number,
            "mode": mode,
            "param": param,
        }
        return self._envelope(
            request,
            self.topics.control(),
            self.topics.channel_status_res(),
        )

    def power(self, state: int, channel_number: int) -> dict:
        """Payload turning a channel on or off."""
        return self._control(CMD_CONTROL_POWER, "power", channel_number, [state])

    def hot_button(self, state: int, channel_number: int) -> dict:
        """Payload starting or stopping on demand recirculation."""
        return self._control(CMD_CONTROL_ON_DEMAND, "onDemand", channel_number, [state])

    def temperature(self, temp: int, channel_number: int) -> dict:
        """Payload setting the domestic hot water target temperature."""
        return self._control(
            CMD_CONTROL_TEMPERATURE, "DHWTemperature", channel_number, [temp]
        )

    def last_will(self) -> dict:
        """Payload published by the broker if this client disappears."""
        return {
            "clientID": self.client_id,
            "event": {
                "additionalValue": self.additional_value,
                "connection": {"os": "A", "status": 0},
                "deviceType": self.device_type,
                "macAddress": self.mac_address,
            },
            "protocolVersion": 1,
            "requestTopic": self.topics.app_connection(),
            "sessionID": "",
        }


# --------------------------------------------------------------------------- #
# Value normalisation
# --------------------------------------------------------------------------- #
# Keys that hold a temperature.  Anything else whose name ends in "Temp" is
# treated as a temperature too, so fields we do not know about yet still end
# up with sane values.
_TEMPERATURE_KEYS = {
    "DHWSettingTemp",
    "avgInletTemp",
    "avgOutletTemp",
    "currentInletTemp",
    "currentOutletTemp",
    "currentInnerTemp",
    "currentSupplyTemp",
    "currentReturnTemp",
    "heatSettingTemp",
    "inletTemp",
    "outletTemp",
    "recirculationSettingTemp",
}

# Keys that the gateway encodes as 1 = on / 2 = off.
_FLAG_KEYS = {
    "powerStatus",
    "onDemandUseFlag",
    "freezeProtectionUse",
    "recirculationFlag",
    "wwsdFlag",
    "DHWUse",
    "consumptionFlag",
}


def _is_temperature_key(key: str) -> bool:
    """Return True when a payload key holds a temperature."""
    return key in _TEMPERATURE_KEYS or key.endswith("Temp")


def _as_number(value: Any) -> float | int | None:
    """Return value as a number, or None when it is not numeric."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _scale_temperature(value: Any, celsius: bool) -> Any:
    """Convert a raw temperature to the unit the gateway reports in.

    Celsius gateways report half degrees; Fahrenheit gateways report whole
    degrees already.
    """
    number = _as_number(value)
    if number is None:
        return value
    return round(number / 2.0, 1) if celsius else float(number)


def _scale_flag(value: Any) -> Any:
    """Convert a 1/2 encoded flag to a boolean."""
    number = _as_number(value)
    if number is None:
        return value
    return number == STATE_ON


class NavienChannel:
    """A single heating channel of a NaviLink gateway."""

    def __init__(self, device: NavienDevice, channel_number: int) -> None:
        """Create an empty channel; data arrives from the gateway later."""
        self.device = device
        self.channel_number = channel_number
        self.channel_info: dict[str, Any] = {}
        self.channel_status: dict[str, Any] = {}
        self.last_update: float | None = None
        self._listeners: list[Callable[[], None]] = []

    # -- listeners ---------------------------------------------------------- #
    def add_listener(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Register a callback fired whenever this channel changes."""
        self._listeners.append(callback)

        def _remove() -> None:
            if callback in self._listeners:
                self._listeners.remove(callback)

        return _remove

    def notify(self) -> None:
        """Tell every listener that this channel changed."""
        for callback in list(self._listeners):
            try:
                callback()
            except Exception:  # pragma: no cover - defensive
                _LOGGER.exception("Error while notifying a NaviLink listener")

    # -- identity ----------------------------------------------------------- #
    @property
    def key(self) -> str:
        """Stable identifier of this channel within the account."""
        return f"{self.device.mac_address}_{self.channel_number}"

    @property
    def name(self) -> str:
        """Human readable name for this channel."""
        if self.device.channel_count > 1:
            return f"{self.device.name} CH{self.channel_number}"
        return self.device.name

    @property
    def temperature_type(self) -> int:
        """Unit system the gateway reports temperatures in."""
        return int(
            self.channel_info.get("temperatureType", TemperatureType.FAHRENHEIT.value)
        )

    @property
    def is_celsius(self) -> bool:
        """Return True when this channel reports Celsius."""
        return self.temperature_type == TemperatureType.CELSIUS.value

    @property
    def unit_type(self) -> int:
        """Numeric Navien product family of this channel."""
        value = self.channel_status.get("unitType")
        if value is None:
            value = self.channel_info.get("unitType", self.channel_info.get("deviceSorting", 0))
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @property
    def model(self) -> str | None:
        """Marketing style model name, when we can map it."""
        try:
            return MODEL_NAMES.get(DeviceSorting(self.unit_type))
        except ValueError:
            return None

    @property
    def unit_count(self) -> int:
        """Number of physical units behind this channel (cascade systems)."""
        for source in (self.channel_status, self.channel_info):
            value = source.get("unitCount")
            if isinstance(value, int) and value > 0:
                return value
        return 1

    @property
    def units(self) -> list[dict[str, Any]]:
        """Per unit status dictionaries."""
        units = self.channel_status.get("unitInfo", {}).get("unitStatusList", [])
        return units if isinstance(units, list) else []

    @property
    def supports_on_demand(self) -> bool:
        """Return True when the hot button / recirculation can be controlled."""
        return self.channel_info.get("onDemandUse") == STATE_ON

    @property
    def has_data(self) -> bool:
        """Return True once the gateway has reported a status at least once."""
        return bool(self.channel_status)

    @property
    def available(self) -> bool:
        """Return True when this channel can currently be talked to."""
        return self.device.account.connected and self.device.present and self.has_data

    # -- values ------------------------------------------------------------- #
    @property
    def power(self) -> bool:
        """Return True when the channel is powered on."""
        return bool(self.channel_status.get("powerStatus", False))

    @property
    def on_demand(self) -> bool:
        """Return True when on demand recirculation is running."""
        return bool(self.channel_status.get("onDemandUseFlag", False))

    @property
    def target_temperature(self) -> float | None:
        """Domestic hot water setpoint, in the gateway's own unit."""
        return _as_number(self.channel_status.get("DHWSettingTemp"))

    @property
    def current_temperature(self) -> float | None:
        """Average outlet temperature across every unit of the channel."""
        value = _as_number(self.channel_status.get("avgOutletTemp"))
        if value:
            return value
        temps = [
            _as_number(unit.get("currentOutletTemp"))
            for unit in self.units
            if _as_number(unit.get("currentOutletTemp")) is not None
        ]
        if temps:
            return round(sum(temps) / len(temps), 1)
        return value

    @property
    def flow_rate(self) -> float | None:
        """Combined hot water flow of the channel, in litres per minute."""
        flows = [
            _as_number(unit.get("DHWFlowRate"))
            for unit in self.units
            if _as_number(unit.get("DHWFlowRate")) is not None
        ]
        if not flows:
            return None
        return round(sum(flows), 2)

    @property
    def gas_instant_usage(self) -> float | None:
        """Combined instantaneous burner output of the channel, in watts."""
        values = [
            _as_number(unit.get("gasInstantUsage"))
            for unit in self.units
            if _as_number(unit.get("gasInstantUsage")) is not None
        ]
        if not values:
            return None
        return round(sum(values), 1)

    @property
    def accumulated_gas_usage(self) -> float | None:
        """Combined lifetime gas consumption of the channel, in cubic metres."""
        values = [
            _as_number(unit.get("accumulatedGasUsage"))
            for unit in self.units
            if _as_number(unit.get("accumulatedGasUsage")) is not None
        ]
        if not values:
            return None
        return round(sum(values), 3)

    @property
    def min_temperature(self) -> float:
        """Lowest setpoint the gateway accepts."""
        value = _as_number(self.channel_info.get("setupDHWTempMin"))
        if value:
            return float(value)
        return FALLBACK_TEMP_LIMITS["celsius" if self.is_celsius else "fahrenheit"][0]

    @property
    def max_temperature(self) -> float:
        """Highest setpoint the gateway accepts."""
        value = _as_number(self.channel_info.get("setupDHWTempMax"))
        if value:
            return float(value)
        return FALLBACK_TEMP_LIMITS["celsius" if self.is_celsius else "fahrenheit"][1]

    @property
    def temperature_step(self) -> float:
        """Smallest setpoint change the gateway accepts."""
        return 0.5 if self.is_celsius else 1.0

    # -- updates ------------------------------------------------------------ #
    def update_info(self, raw_info: dict[str, Any]) -> None:
        """Store freshly received channel information."""
        info = dict(raw_info)
        celsius = int(info.get("temperatureType", TemperatureType.FAHRENHEIT.value)) == (
            TemperatureType.CELSIUS.value
        )
        for key in ("setupDHWTempMin", "setupDHWTempMax", "setupTempMin", "setupTempMax"):
            if key in info:
                info[key] = _scale_temperature(info[key], celsius)
        self.channel_info = info

    def update_status(self, raw_status: dict[str, Any]) -> bool:
        """Store a freshly received channel status.

        Returns True when anything actually changed, so callers can skip
        pointless state writes.
        """
        status = self._normalize_status(raw_status)
        changed = status != self.channel_status
        self.channel_status = status
        self.last_update = time.time()
        return changed

    def apply_optimistic(self, **values: Any) -> None:
        """Apply a locally predicted value so the UI reacts immediately."""
        if not self.channel_status:
            return
        self.channel_status = {**self.channel_status, **values}
        self.notify()

    def _normalize_status(self, raw_status: dict[str, Any]) -> dict[str, Any]:
        """Convert a raw status payload into real world units."""
        status = dict(raw_status)
        celsius = self.is_celsius
        unit_type = _as_number(status.get("unitType"))
        if unit_type is None:
            unit_type = self.unit_type
        gas_factor = 10 if int(unit_type) in HIGH_RESOLUTION_GAS_TYPES else 1

        for key, value in list(status.items()):
            if key in _FLAG_KEYS:
                status[key] = _scale_flag(value)
            elif _is_temperature_key(key):
                status[key] = _scale_temperature(value, celsius)

        if (calorie := _as_number(status.get("avgCalorie"))) is not None:
            status["avgCalorie"] = round(calorie / 2.0, 1)

        unit_info = status.get("unitInfo")
        if isinstance(unit_info, dict):
            unit_list = unit_info.get("unitStatusList")
            if isinstance(unit_list, list):
                status["unitInfo"] = {
                    **unit_info,
                    "unitStatusList": [
                        self._normalize_unit(unit, celsius, gas_factor)
                        for unit in unit_list
                        if isinstance(unit, dict)
                    ],
                }
        return status

    @staticmethod
    def _normalize_unit(
        raw_unit: dict[str, Any], celsius: bool, gas_factor: int
    ) -> dict[str, Any]:
        """Convert a raw per unit status payload into real world units.

        The gateway always sends the same raw scaling regardless of whether it
        is configured for Celsius or Fahrenheit, so everything is normalised to
        SI here and Home Assistant takes care of presenting it in the unit the
        user prefers.
        """
        unit = dict(raw_unit)

        for key, value in list(unit.items()):
            if key in _FLAG_KEYS:
                unit[key] = _scale_flag(value)
            elif _is_temperature_key(key):
                unit[key] = _scale_temperature(value, celsius)

        # Raw flow is in tenths of a litre per minute.
        if (flow := _as_number(unit.get("DHWFlowRate"))) is not None:
            unit["DHWFlowRate"] = round(flow / 10.0, 2)

        # Raw accumulated gas is in tenths of a cubic metre.
        if (gas := _as_number(unit.get("accumulatedGasUsage"))) is not None:
            unit["accumulatedGasUsage"] = round(gas / 10.0, 3)

        # Raw instantaneous gas usage is a heat rate in kcal/h once the family
        # specific factor is applied.  Report it in watts.
        if (instant := _as_number(unit.get("gasInstantUsage"))) is not None:
            unit["gasInstantUsage"] = round(
                instant * gas_factor * KCAL_PER_HOUR_TO_WATT, 1
            )

        return unit

    # -- commands ----------------------------------------------------------- #
    async def async_set_power(self, state: bool) -> None:
        """Turn the channel on or off."""
        await self.device.async_send_power(self.channel_number, state)
        self.apply_optimistic(powerStatus=state)
        await self.async_refresh()

    async def async_set_on_demand(self, state: bool) -> None:
        """Start or stop on demand recirculation."""
        await self.device.async_send_on_demand(self.channel_number, state)
        self.apply_optimistic(onDemandUseFlag=state)
        await self.async_refresh()

    async def async_set_target_temperature(self, temperature: float) -> None:
        """Set the domestic hot water setpoint, in the gateway's own unit."""
        raw = round(temperature * 2) if self.is_celsius else round(temperature)
        await self.device.async_send_temperature(self.channel_number, raw)
        self.apply_optimistic(DHWSettingTemp=round(temperature, 1))
        await self.async_refresh()

    async def async_refresh(self) -> None:
        """Ask the gateway for a fresh status of this channel."""
        await self.device.async_request_status(self.channel_number, wait=True)


class NavienDevice:
    """A NaviLink gateway and the channels behind it."""

    def __init__(self, account: NavienAccount, raw_device: dict[str, Any]) -> None:
        """Create a gateway wrapper around a REST device list entry."""
        self.account = account
        self.raw: dict[str, Any] = {}
        self.channels: dict[int, NavienChannel] = {}
        self.topics: Topics | None = None
        self.messages: Messages | None = None
        # False once the gateway disappears from the account's device list.
        self.present = True
        self.update_raw(raw_device)

    def update_raw(self, raw_device: dict[str, Any]) -> None:
        """Store the latest REST description of this gateway."""
        self.raw = raw_device

    # -- identity ----------------------------------------------------------- #
    @property
    def info(self) -> dict[str, Any]:
        """The deviceInfo block of the REST device list entry."""
        return self.raw.get("deviceInfo", {})

    @property
    def mac_address(self) -> str:
        """MAC address of the gateway; the stable id of a NaviLink device."""
        return self.info.get("macAddress", "")

    @property
    def name(self) -> str:
        """Name the user gave this gateway in the NaviLink app."""
        return self.info.get("deviceName") or f"NaviLink {self.mac_address}"

    @property
    def device_type(self) -> int:
        """Numeric gateway type."""
        try:
            return int(self.info.get("deviceType", 1))
        except (TypeError, ValueError):
            return 1

    @property
    def channel_count(self) -> int:
        """Number of channels this gateway exposes."""
        return len(self.channels)

    @property
    def sw_version(self) -> str | None:
        """Firmware version reported for the gateway, when available."""
        for key in ("swVersion", "firmwareVersion", "version", "additionalValue"):
            value = self.info.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    # -- connection scaffolding --------------------------------------------- #
    def build_protocol(self, user_info: dict[str, Any], client_id: str) -> None:
        """(Re)build the topic and payload builders for a new connection."""
        self.topics = Topics(user_info, self.raw, client_id)
        self.messages = Messages(self.raw, client_id, self.topics)

    def update_channel_info(self, channel_list: list[dict[str, Any]]) -> None:
        """Merge freshly received channel information.

        Existing NavienChannel objects are updated in place so that entities
        holding a reference keep working across reconnects.
        """
        seen: set[int] = set()
        for entry in channel_list:
            if not isinstance(entry, dict):
                continue
            try:
                number = int(entry.get("channelNumber", 0))
            except (TypeError, ValueError):
                continue
            seen.add(number)
            channel = self.channels.get(number)
            if channel is None:
                channel = NavienChannel(self, number)
                self.channels[number] = channel
            channel.update_info(entry.get("channel", entry))

        for number in list(self.channels):
            if number not in seen:
                _LOGGER.debug(
                    "Channel %s of %s disappeared from the gateway", number, self.name
                )

    # -- requests ----------------------------------------------------------- #
    async def async_request_status(
        self, channel_number: int, wait: bool = False
    ) -> None:
        """Ask the gateway for the status of one channel."""
        channel = self.channels.get(channel_number)
        if channel is None or self.messages is None or self.topics is None:
            return
        payload = self.messages.channel_status(channel_number, channel.unit_count)
        await self.account.async_publish(
            self.topics.channel_status_req(), payload, wait=wait
        )

    async def async_request_channel_info(self) -> None:
        """Ask the gateway to describe its channels."""
        if self.messages is None or self.topics is None:
            return
        await self.account.async_publish(
            self.topics.start(), self.messages.channel_info(), wait=True
        )

    async def async_send_power(self, channel_number: int, state: bool) -> None:
        """Send a power on/off command."""
        if self.messages is None or self.topics is None:
            raise NavienConnectionError("Not connected to the NaviLink cloud")
        payload = self.messages.power(
            STATE_ON if state else STATE_OFF_VALUE, channel_number
        )
        await self.account.async_publish(self.topics.control(), payload, wait=True)

    async def async_send_on_demand(self, channel_number: int, state: bool) -> None:
        """Send an on demand recirculation command."""
        if self.messages is None or self.topics is None:
            raise NavienConnectionError("Not connected to the NaviLink cloud")
        payload = self.messages.hot_button(
            STATE_ON if state else STATE_OFF_VALUE, channel_number
        )
        await self.account.async_publish(self.topics.control(), payload, wait=True)

    async def async_send_temperature(self, channel_number: int, raw_temp: int) -> None:
        """Send a domestic hot water setpoint command."""
        if self.messages is None or self.topics is None:
            raise NavienConnectionError("Not connected to the NaviLink cloud")
        payload = self.messages.temperature(raw_temp, channel_number)
        await self.account.async_publish(self.topics.control(), payload, wait=True)


class NavienAccount:
    """A NaviLink account: one login, one MQTT link, every gateway."""

    def __init__(
        self,
        username: str,
        password: str,
        session: aiohttp.ClientSession,
        aws_cert_path: str,
        polling_interval: int = DEFAULT_POLLING_INTERVAL,
    ) -> None:
        """Create a client for one NaviLink account."""
        self.username = username
        self.password = password
        self.aws_cert_path = aws_cert_path
        self.polling_interval = polling_interval

        self._session = session
        self._loop = asyncio.get_running_loop()
        self._devices: dict[str, NavienDevice] = {}
        self._client: mqtt.AWSIoTMQTTClient | None = None
        self._client_id = ""
        self._client_lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Event] = {}
        self._disconnected = asyncio.Event()
        self._runner: asyncio.Task | None = None
        self._closing = False
        self._connected = False
        self._last_session_value = 0
        self._auth_failure_callback: Callable[[], None] | None = None

        self.user_info: dict[str, Any] | None = None

    # -- public state ------------------------------------------------------- #
    @property
    def connected(self) -> bool:
        """Return True while the MQTT link is up."""
        return self._connected

    @property
    def devices(self) -> dict[str, NavienDevice]:
        """Every gateway known to this account, keyed by MAC address."""
        return self._devices

    @property
    def channels(self) -> list[NavienChannel]:
        """Every channel of every gateway of this account."""
        return [
            channel
            for device in self._devices.values()
            for channel in device.channels.values()
        ]

    def set_auth_failure_callback(self, callback: Callable[[], None]) -> None:
        """Register a callback fired when the cloud rejects our credentials."""
        self._auth_failure_callback = callback

    def set_polling_interval(self, interval: int) -> None:
        """Change how often the gateways are polled."""
        self.polling_interval = interval

    # -- lifecycle ---------------------------------------------------------- #
    async def async_login(self) -> list[dict[str, Any]]:
        """Sign in and return the raw REST device list (used by the config flow)."""
        await self._async_login()
        return [device.raw for device in self._devices.values()]

    async def async_setup(self) -> None:
        """Sign in, connect and load the initial state of every gateway."""
        await self._async_login()
        await self._async_connect()
        self._runner = self._loop.create_task(self._async_run())

    async def async_stop(self) -> None:
        """Shut the client down for good."""
        self._closing = True
        if self._runner is not None:
            self._runner.cancel()
            try:
                await self._runner
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._runner = None
        await self._async_teardown_client()
        self._set_connected(False)

    async def async_refresh_all(self) -> None:
        """Request a fresh status for every channel of every gateway."""
        for device in list(self._devices.values()):
            for number in list(device.channels):
                await device.async_request_status(number, wait=False)

    # -- REST --------------------------------------------------------------- #
    async def _async_login(self) -> None:
        """Sign in to the NaviLink REST API."""
        try:
            async with self._session.post(
                f"{NAVIEN_API_BASE}/user/sign-in",
                json={"userId": self.username, "password": self.password},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status in (401, 403):
                    raise NavienAuthError("NaviLink rejected the credentials")
                if response.status != 200:
                    raise NavienConnectionError(
                        f"Unexpected HTTP {response.status} while signing in"
                    )
                data = await response.json(content_type=None)
        except (NavienAuthError, NavienConnectionError):
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise NavienConnectionError(f"Unable to reach the NaviLink cloud: {err}") from err
        except Exception as err:  # noqa: BLE001
            raise NavienResponseError(f"Unreadable sign-in response: {err}") from err

        message = str(data.get("msg", "")).upper()
        user_info = data.get("data")
        if not isinstance(user_info, dict):
            if message:
                raise NavienAuthError(f"NaviLink sign-in failed: {message}")
            raise NavienResponseError("NaviLink sign-in returned no user data")

        self.user_info = user_info
        await self._async_fetch_devices()

    async def _async_fetch_devices(self) -> None:
        """Refresh the list of gateways attached to the account."""
        token = (self.user_info or {}).get("token", {}).get("accessToken", "")
        try:
            async with self._session.post(
                f"{NAVIEN_API_BASE}/device/list",
                headers={"Authorization": token},
                json={"offset": 0, "count": 100, "userId": self.username},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status in (401, 403):
                    raise NavienAuthError("NaviLink rejected the access token")
                if response.status != 200:
                    raise NavienConnectionError(
                        f"Unexpected HTTP {response.status} while listing devices"
                    )
                data = await response.json(content_type=None)
        except (NavienAuthError, NavienConnectionError):
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise NavienConnectionError(f"Unable to list NaviLink devices: {err}") from err
        except Exception as err:  # noqa: BLE001
            raise NavienResponseError(f"Unreadable device list response: {err}") from err

        device_list = data.get("data")
        if not isinstance(device_list, list):
            raise NavienResponseError("NaviLink returned no device list")

        seen: set[str] = set()
        for raw_device in device_list:
            if not isinstance(raw_device, dict):
                continue
            mac = raw_device.get("deviceInfo", {}).get("macAddress")
            if not mac:
                continue
            seen.add(mac)
            if (device := self._devices.get(mac)) is not None:
                device.update_raw(raw_device)
                device.present = True
            else:
                self._devices[mac] = NavienDevice(self, raw_device)

        for mac, device in self._devices.items():
            if mac not in seen:
                device.present = False
                _LOGGER.warning(
                    "NaviLink gateway %s (%s) is no longer on the account", device.name, mac
                )

        if not self._devices:
            raise NavienNoDevicesError("No NaviLink gateways found on this account")

        _LOGGER.debug(
            "NaviLink account holds %s gateway(s): %s",
            len(self._devices),
            ", ".join(f"{d.name} ({mac})" for mac, d in self._devices.items()),
        )

    # -- MQTT --------------------------------------------------------------- #
    async def _async_connect(self) -> None:
        """Open the MQTT link and load the initial state of every gateway."""
        token = (self.user_info or {}).get("token", {})
        access_key = token.get("accessKeyId")
        secret_key = token.get("secretKey")
        session_token = token.get("sessionToken")
        if not (access_key and secret_key and session_token):
            raise NavienAuthError("NaviLink did not hand out MQTT credentials")

        self._client_id = str(uuid.uuid4())
        for device in self._devices.values():
            device.build_protocol(self.user_info or {}, self._client_id)

        client = mqtt.AWSIoTMQTTClient(
            clientID=self._client_id, protocolType=4, useWebsocket=True, cleanSession=True
        )
        client.configureEndpoint(hostName=AWS_IOT_ENDPOINT, portNumber=AWS_IOT_PORT)
        client.configureUsernamePassword(
            username="?SDK=Android&Version=2.16.12", password=None
        )

        first = next(iter(self._devices.values()), None)
        if first is not None and first.topics is not None and first.messages is not None:
            client.configureLastWill(
                topic=first.topics.app_connection(),
                payload=json.dumps(first.messages.last_will(), separators=(",", ":")),
                QoS=1,
                retain=False,
            )

        await self._loop.run_in_executor(
            None, client.configureCredentials, self.aws_cert_path
        )
        client.configureIAMCredentials(
            AWSAccessKeyID=access_key,
            AWSSecretAccessKey=secret_key,
            AWSSessionToken=session_token,
        )
        client.configureConnectDisconnectTimeout(15)
        client.configureMQTTOperationTimeout(10)
        # Never replay stale commands after a reconnect.
        client.configureOfflinePublishQueueing(0)
        client.onOffline = self._on_offline
        client.onOnline = self._on_online

        self._client = client
        self._disconnected.clear()
        try:
            await self._loop.run_in_executor(None, client.connect)
        except Exception as err:  # noqa: BLE001
            self._client = None
            raise NavienConnectionError(f"Unable to open the NaviLink MQTT link: {err}") from err

        self._set_connected(True)
        await self._async_subscribe_all()
        await self._async_bootstrap()

    async def _async_subscribe_all(self) -> None:
        """Subscribe to every topic of every gateway in one executor hop."""
        client = self._client
        if client is None:
            raise NavienConnectionError("Not connected to the NaviLink cloud")

        subscriptions: list[tuple[str, Callable]] = []
        for device in self._devices.values():
            topics = device.topics
            if topics is None:
                continue
            subscriptions.extend(
                [
                    (topics.channel_info_res(), self._wrap(self._handle_channel_info, device)),
                    (topics.channel_info_sub(), self._wrap(self._handle_channel_info, device)),
                    (topics.channel_status_res(), self._wrap(self._handle_channel_status, device)),
                    (topics.channel_status_sub(), self._wrap(self._handle_channel_status, device)),
                    (topics.control_fail(), self._wrap(self._handle_control_fail, device)),
                    (topics.connection(), self._wrap(self._handle_generic, device)),
                ]
            )

        def _subscribe() -> None:
            for topic, callback in subscriptions:
                client.subscribe(topic=topic, QoS=1, callback=callback)

        async with self._client_lock:
            await self._loop.run_in_executor(None, _subscribe)

    async def _async_bootstrap(self) -> None:
        """Load channel information and a first status for every gateway."""
        usable = 0
        for device in self._devices.values():
            if not device.present:
                continue
            try:
                await device.async_request_channel_info()
            except NavienError as err:
                _LOGGER.warning("No channel information from %s: %s", device.name, err)
                continue
            if not device.channels:
                _LOGGER.warning(
                    "NaviLink gateway %s did not report any channel; it may be offline",
                    device.name,
                )
                continue
            for number in list(device.channels):
                await device.async_request_status(number, wait=True)
                await asyncio.sleep(REQUEST_SPACING)
            usable += 1

        if usable == 0:
            raise NavienConnectionError(
                "None of the NaviLink gateways on this account responded"
            )

    async def _async_teardown_client(self) -> None:
        """Close the MQTT link, ignoring anything that goes wrong doing so."""
        client, self._client = self._client, None
        for event in list(self._pending.values()):
            event.set()
        self._pending.clear()
        if client is None:
            return
        try:
            await self._loop.run_in_executor(None, client.disconnect)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Ignoring error while disconnecting from NaviLink: %s", err)

    # -- background session ------------------------------------------------- #
    async def _async_run(self) -> None:
        """Keep the account connected for as long as the entry is loaded."""
        delay = RECONNECT_INITIAL_DELAY
        try:
            while not self._closing:
                try:
                    await self._async_session()
                except asyncio.CancelledError:
                    raise
                except NavienAuthError as err:
                    _LOGGER.error("NaviLink rejected the stored credentials: %s", err)
                    if self._auth_failure_callback is not None:
                        self._auth_failure_callback()
                    return
                except Exception as err:  # noqa: BLE001
                    _LOGGER.info("NaviLink session ended (%s), reconnecting", err)

                if self._closing:
                    return

                await self._async_teardown_client()
                self._set_connected(False)

                while not self._closing:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, RECONNECT_MAX_DELAY)
                    try:
                        await self._async_login()
                        await self._async_connect()
                    except asyncio.CancelledError:
                        raise
                    except NavienAuthError as err:
                        _LOGGER.error("NaviLink rejected the stored credentials: %s", err)
                        if self._auth_failure_callback is not None:
                            self._auth_failure_callback()
                        return
                    except Exception as err:  # noqa: BLE001
                        _LOGGER.debug("NaviLink reconnect attempt failed: %s", err)
                        await self._async_teardown_client()
                        continue
                    delay = RECONNECT_INITIAL_DELAY
                    _LOGGER.info("Reconnected to the NaviLink cloud")
                    break
        finally:
            await self._async_teardown_client()
            self._set_connected(False)

    async def _async_session(self) -> None:
        """Run one connected session; returns once the link has to be rebuilt."""
        self._disconnected.clear()
        tasks = [
            self._loop.create_task(self._async_poll_loop()),
            self._loop.create_task(self._disconnected.wait()),
            self._loop.create_task(asyncio.sleep(CREDENTIAL_REFRESH_SECONDS)),
        ]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        for task in done:
            if (error := task.exception()) is not None:
                raise error

        if tasks[2] in done:
            _LOGGER.debug("Refreshing NaviLink credentials on schedule")

    async def _async_poll_loop(self) -> None:
        """Ask every channel for its status on the configured interval."""
        while True:
            await asyncio.sleep(self.polling_interval)
            for device in list(self._devices.values()):
                if not device.present:
                    continue
                for number in list(device.channels):
                    await device.async_request_status(number, wait=False)
                    await asyncio.sleep(REQUEST_SPACING)

    # -- publish / response correlation ------------------------------------- #
    def _next_session_id(self) -> str:
        """Return a session id that is unique even within the same millisecond."""
        value = int(time.time() * 1000)
        if value <= self._last_session_value:
            value = self._last_session_value + 1
        self._last_session_value = value
        return str(value)

    async def async_publish(
        self, topic: str, payload: dict[str, Any], wait: bool = False
    ) -> bool:
        """Publish a payload and optionally wait for the matching response."""
        client = self._client
        if client is None or not self._connected:
            raise NavienConnectionError("Not connected to the NaviLink cloud")

        session_id = self._next_session_id()
        payload["sessionID"] = session_id
        event: asyncio.Event | None = None
        if wait:
            event = asyncio.Event()
            self._pending[session_id] = event

        try:
            body = json.dumps(payload, separators=(",", ":"))

            def _publish() -> None:
                client.publish(topic, body, 1)

            async with self._client_lock:
                await self._loop.run_in_executor(None, _publish)
        except Exception as err:  # noqa: BLE001
            self._pending.pop(session_id, None)
            self._handle_offline()
            raise NavienConnectionError(f"Publishing to NaviLink failed: {err}") from err

        if event is None:
            return True

        try:
            await asyncio.wait_for(event.wait(), timeout=RESPONSE_TIMEOUT)
        except asyncio.TimeoutError:
            _LOGGER.debug("No NaviLink response for %s on %s", session_id, topic)
            return False
        finally:
            self._pending.pop(session_id, None)
        return True

    def _resolve(self, session_id: Any) -> None:
        """Release whoever is waiting for this response."""
        if not isinstance(session_id, str):
            return
        if (event := self._pending.get(session_id)) is not None:
            event.set()

    # -- message handling --------------------------------------------------- #
    def _wrap(self, handler: Callable[[NavienDevice, dict], None], device: NavienDevice):
        """Bind an MQTT callback to a gateway and hop back onto the event loop."""

        def _callback(client, userdata, message) -> None:  # noqa: ANN001
            try:
                payload = json.loads(message.payload)
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Ignoring unparsable NaviLink message on %s", message.topic)
                return
            if not isinstance(payload, dict):
                return
            self._call_soon(handler, device, payload)

        return _callback

    def _call_soon(self, callback: Callable, *args: Any) -> None:
        """Run a callback on the event loop from the MQTT thread."""
        try:
            self._loop.call_soon_threadsafe(callback, *args)
        except RuntimeError:  # pragma: no cover - loop already closed
            pass

    def _handle_channel_info(self, device: NavienDevice, payload: dict) -> None:
        """Store channel information reported by a gateway."""
        channel_list = (
            payload.get("response", {}).get("channelInfo", {}).get("channelList", [])
        )
        if isinstance(channel_list, list) and channel_list:
            device.update_channel_info(channel_list)
        self._resolve(payload.get("sessionID"))

    def _handle_channel_status(self, device: NavienDevice, payload: dict) -> None:
        """Store a channel status reported by a gateway."""
        status = payload.get("response", {}).get("channelStatus", {})
        if isinstance(status, dict) and status:
            number = status.get("channelNumber")
            channel = device.channels.get(number) if number is not None else None
            if channel is None and len(device.channels) == 1:
                channel = next(iter(device.channels.values()))
            if channel is not None:
                data = status.get("channel", status)
                if isinstance(data, dict):
                    try:
                        if channel.update_status(data):
                            channel.notify()
                    except Exception:  # noqa: BLE001
                        _LOGGER.exception(
                            "Could not process the status of %s", channel.name
                        )
        self._resolve(payload.get("sessionID"))

    def _handle_control_fail(self, device: NavienDevice, payload: dict) -> None:
        """Log a control command the gateway refused."""
        _LOGGER.warning(
            "NaviLink gateway %s rejected a command: %s", device.name, payload
        )
        self._resolve(payload.get("sessionID"))

    def _handle_generic(self, device: NavienDevice, payload: dict) -> None:
        """Log anything else a gateway sends us."""
        _LOGGER.debug("NaviLink message from %s: %s", device.name, payload)
        self._resolve(payload.get("sessionID"))

    # -- connection state --------------------------------------------------- #
    def _on_online(self) -> None:
        """MQTT thread callback: the link came up."""
        self._call_soon(self._set_connected, True)

    def _on_offline(self) -> None:
        """MQTT thread callback: the link went down."""
        self._call_soon(self._handle_offline)

    def _handle_offline(self) -> None:
        """Mark the link as down so the runner rebuilds it."""
        if self._closing:
            return
        self._set_connected(False)
        self._disconnected.set()

    def _set_connected(self, value: bool) -> None:
        """Update the connection flag and refresh entity availability."""
        if self._connected == value:
            return
        self._connected = value
        _LOGGER.debug("NaviLink link is now %s", "up" if value else "down")
        for channel in self.channels:
            channel.notify()
