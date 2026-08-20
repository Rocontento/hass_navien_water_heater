"""Protocol level tests for the NaviLink client."""

import asyncio
import json

import pytest

from custom_components.navien_water_heater import navien_api as api

from . import fake_cloud


@pytest.fixture
async def account(monkeypatch):
    """A connected account talking to the simulated cloud."""
    fake_cloud.FakeClient.instances.clear()
    monkeypatch.setattr(api.mqtt, "AWSIoTMQTTClient", fake_cloud.FakeClient)
    client = api.NavienAccount(
        "user@example.com", "pw", fake_cloud.FakeSession(), "ca.pem", polling_interval=1
    )
    await client.async_setup()
    yield client
    await client.async_stop()


def channels_by_mac(account):
    """Index the account's channels by gateway MAC."""
    return {channel.device.mac_address: channel for channel in account.channels}


async def test_every_gateway_on_the_account_is_discovered(account):
    assert sorted(account.devices) == ["AA11", "BB22"]
    assert len(account.channels) == 2
    by_mac = channels_by_mac(account)
    assert by_mac["AA11"].name == "Calentador Cocina"
    assert by_mac["BB22"].name == "Calentador Bano"


async def test_fahrenheit_gateway_values(account):
    channel = channels_by_mac(account)["AA11"]
    assert channel.target_temperature == 120
    assert channel.current_temperature == 118
    assert (channel.min_temperature, channel.max_temperature) == (90.0, 140.0)
    assert channel.temperature_step == 1.0
    assert channel.power is True
    assert channel.on_demand is False
    assert channel.supports_on_demand is True
    assert channel.channel_status["avgCalorie"] == 42.0
    unit = channel.units[0]
    assert unit["DHWFlowRate"] == 2.5          # tenths of a litre per minute
    assert unit["accumulatedGasUsage"] == 123.4  # tenths of a cubic metre
    assert unit["gasInstantUsage"] == 116.3      # kcal/h turned into watts


async def test_celsius_gateway_values(account):
    """Celsius gateways encode half degrees and high resolution gas usage."""
    channel = channels_by_mac(account)["BB22"]
    assert channel.model == "NFC"
    assert channel.target_temperature == 50.0
    assert channel.current_temperature == 49.0
    assert (channel.min_temperature, channel.max_temperature) == (40.0, 65.0)
    assert channel.temperature_step == 0.5
    assert channel.units[0]["gasInstantUsage"] == 58.1


async def test_commands_are_addressed_to_the_right_gateway(account):
    by_mac = channels_by_mac(account)
    client = fake_cloud.FakeClient.instances[-1]

    client.published.clear()
    await by_mac["BB22"].async_set_target_temperature(52.5)
    request = json.loads(client.published[0][1])["request"]
    assert request["macAddress"] == "BB22"
    assert request["control"]["param"] == [105]   # half degrees on the wire

    client.published.clear()
    await by_mac["AA11"].async_set_target_temperature(125)
    request = json.loads(client.published[0][1])["request"]
    assert request["macAddress"] == "AA11"
    assert request["control"]["param"] == [125]   # whole degrees on the wire

    client.published.clear()
    await by_mac["AA11"].async_set_power(False)
    request = json.loads(client.published[0][1])["request"]
    assert request["control"]["mode"] == "power"
    assert request["control"]["param"] == [2]


async def test_reconnect_keeps_channel_objects_and_listeners(account):
    """Entities hold on to channel objects, so a reconnect must not replace them."""
    by_mac = channels_by_mac(account)
    channel = by_mac["AA11"]
    updates = []
    remove = channel.add_listener(lambda: updates.append(1))
    identity = id(channel)

    account._handle_offline()
    await asyncio.sleep(0.1)
    assert channel.available is False

    await asyncio.sleep(6.5)   # the first reconnect attempt is five seconds out
    assert account.connected is True
    assert id(channels_by_mac(account)["AA11"]) == identity
    # The simulated cloud reorders its device list on the second call, which is
    # what used to make the old index based lookup point at the wrong heater.
    assert channels_by_mac(account)["AA11"].device.name == "Calentador Cocina"
    assert updates
    remove()


async def test_polling_covers_every_gateway(account):
    client = fake_cloud.FakeClient.instances[-1]
    client.published.clear()
    await asyncio.sleep(1.6)
    macs = {json.loads(payload)["request"]["macAddress"] for _, payload in client.published}
    assert macs == {"AA11", "BB22"}
