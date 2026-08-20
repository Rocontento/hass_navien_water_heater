"""Integration level tests: setup, migration, services, options and teardown."""

import json

from homeassistant.const import UnitOfTemperature
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.navien_water_heater.const import DOMAIN


async def _setup(hass, entry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _last_control(client):
    """Return the most recent control request published to the broker."""
    for _topic, payload in reversed(client.published):
        request = json.loads(payload)["request"]
        if "control" in request:
            return request
    raise AssertionError("no control command was published")


async def test_every_heater_gets_its_own_device_and_entities(
    hass, fake_navilink, config_entry
):
    await _setup(hass, config_entry)

    entities = er.async_entries_for_config_entry(
        er.async_get(hass), config_entry.entry_id
    )
    water_heaters = [item for item in entities if item.domain == "water_heater"]
    assert len(water_heaters) == 2

    devices = dr.async_entries_for_config_entry(
        dr.async_get(hass), config_entry.entry_id
    )
    assert {device.model for device in devices} == {"NPE-2", "NFC"}

    macs = set()
    for item in water_heaters:
        state = hass.states.get(item.entity_id)
        assert state.state != "unavailable"
        assert state.attributes["current_temperature"] is not None
        macs.add(state.attributes["mac_address"])
    assert macs == {"AA11", "BB22"}


async def test_setpoint_is_converted_for_the_gateway(hass, fake_navilink, config_entry):
    """Home Assistant is metric here while one gateway reports Fahrenheit."""
    assert hass.config.units.temperature_unit == UnitOfTemperature.CELSIUS
    await _setup(hass, config_entry)
    client = fake_navilink.FakeClient.instances[-1]

    client.published.clear()
    await hass.services.async_call(
        "water_heater",
        "set_temperature",
        {"entity_id": "water_heater.calentador_cocina", "temperature": 50},
        blocking=True,
    )
    await hass.async_block_till_done()
    request = _last_control(client)
    assert request["macAddress"] == "AA11"
    assert request["control"]["param"] == [122]   # 50 degC is 122 degF

    client.published.clear()
    await hass.services.async_call(
        "water_heater",
        "set_temperature",
        {"entity_id": "water_heater.calentador_bano", "temperature": 47.5},
        blocking=True,
    )
    await hass.async_block_till_done()
    request = _last_control(client)
    assert request["macAddress"] == "BB22"
    assert request["control"]["param"] == [95]    # half degrees, no conversion


async def test_switches_and_slider_reach_the_right_heater(
    hass, fake_navilink, config_entry
):
    await _setup(hass, config_entry)
    client = fake_navilink.FakeClient.instances[-1]

    client.published.clear()
    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": "switch.calentador_bano_recirculation"},
        blocking=True,
    )
    await hass.async_block_till_done()
    request = _last_control(client)
    assert request["macAddress"] == "BB22"
    assert request["control"]["mode"] == "onDemand"
    assert request["control"]["param"] == [1]

    client.published.clear()
    await hass.services.async_call(
        "number",
        "set_value",
        # The slider is shown in Home Assistant's unit; 55 degC is 131 degF.
        {"entity_id": "number.calentador_cocina_target_temperature", "value": 55},
        blocking=True,
    )
    await hass.async_block_till_done()
    request = _last_control(client)
    assert request["macAddress"] == "AA11"
    assert request["control"]["param"] == [131]

    client.published.clear()
    await hass.services.async_call(
        "water_heater",
        "turn_off",
        {"entity_id": "water_heater.calentador_cocina"},
        blocking=True,
    )
    await hass.async_block_till_done()
    request = _last_control(client)
    assert request["control"]["mode"] == "power"
    assert request["control"]["param"] == [2]


async def test_options_change_polling_without_reconnecting(
    hass, fake_navilink, config_entry
):
    await _setup(hass, config_entry)
    account = hass.data[DOMAIN][config_entry.entry_id]
    client_before = fake_navilink.FakeClient.instances[-1]

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"polling_interval": 90}
    )
    await hass.async_block_till_done()

    assert result["type"] == "create_entry"
    assert account.polling_interval == 90
    assert fake_navilink.FakeClient.instances[-1] is client_before


async def test_unload_stops_the_client(hass, fake_navilink, config_entry):
    await _setup(hass, config_entry)
    account = hass.data[DOMAIN][config_entry.entry_id]
    assert account.connected

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert not account.connected
    assert config_entry.entry_id not in hass.data.get(DOMAIN, {})
    assert hass.states.get("water_heater.calentador_cocina").state == "unavailable"


async def test_diagnostics_do_not_leak_secrets(hass, fake_navilink, config_entry):
    from custom_components.navien_water_heater.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    await _setup(hass, config_entry)
    data = await async_get_config_entry_diagnostics(hass, config_entry)
    dumped = json.dumps(data)

    assert "pw" not in json.dumps(data["entry"])
    assert "AA11" not in dumped and "BB22" not in dumped
    assert data["connected"] is True
    assert len(data["devices"]) == 2


async def test_config_flow_creates_one_entry_for_the_account(hass, fake_navilink):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == "form"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"username": "user@example.com", "password": "pw", "polling_interval": 45},
    )
    await hass.async_block_till_done()

    assert result["type"] == "create_entry"
    assert result["data"] == {"username": "user@example.com", "password": "pw"}
    assert result["options"] == {"polling_interval": 45}


async def test_migration_folds_per_heater_entries_into_one(hass, fake_navilink):
    """Version 1 kept one entry per heater, each pointing at a list index."""
    first = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        unique_id="navien_user@example.com_AA11",
        title="navien_user@example.com_AA11",
        data={
            "username": "user@example.com",
            "password": "pw",
            "device_index": 0,
            "polling_interval": 15,
        },
        entry_id="a" * 32,
    )
    second = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        unique_id="navien_user@example.com_BB22",
        title="navien_user@example.com_BB22",
        data={
            "username": "user@example.com",
            "password": "pw",
            "device_index": 1,
            "polling_interval": 15,
        },
        entry_id="b" * 32,
    )
    first.add_to_hass(hass)
    second.add_to_hass(hass)

    # Setting up the domain migrates every entry it owns, so one call is enough.
    await hass.config_entries.async_setup(first.entry_id)
    await hass.async_block_till_done()

    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
    survivor = entries[0]
    assert survivor.version == 2
    assert survivor.unique_id == "user@example.com"
    assert "device_index" not in survivor.data
    assert survivor.options["polling_interval"] == 15

    # The one surviving entry still covers both heaters.
    entities = er.async_entries_for_config_entry(er.async_get(hass), survivor.entry_id)
    assert len([item for item in entities if item.domain == "water_heater"]) == 2


async def test_unknown_fields_are_exposed_and_flags_are_not_numbers(
    hass, fake_navilink, config_entry
):
    """Anything the gateway reports should be reachable, in the right platform."""
    await _setup(hass, config_entry)
    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, config_entry.entry_id)
    by_unique_id = {item.unique_id: item for item in entities}

    # A curated but rarely present field becomes a disabled diagnostic sensor.
    fan = by_unique_id["AA1111fanRPM"]
    assert fan.domain == "sensor"
    assert fan.disabled_by is er.RegistryEntryDisabler.INTEGRATION

    # A field nothing knows about still gets an entity, disabled by default.
    unknown = by_unique_id["AA1111someNewValue"]
    assert unknown.domain == "sensor"
    assert unknown.original_name == "Some new value"
    assert unknown.disabled_by is er.RegistryEntryDisabler.INTEGRATION

    # On/off flags belong to binary_sensor, never to a numeric sensor.
    assert "AA111wwsdFlag" in by_unique_id
    assert by_unique_id["AA111wwsdFlag"].domain == "binary_sensor"
    assert hass.states.get("binary_sensor.calentador_cocina_warm_weather_shutdown").state == "off"
    assert not [
        item for item in entities
        if item.domain == "sensor" and item.unique_id.endswith("freezeProtectionUse")
    ]
