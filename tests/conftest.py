"""Shared fixtures for the Navien NaviLink tests."""

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.navien_water_heater import navien_api as api
from custom_components.navien_water_heater.const import DOMAIN

from . import fake_cloud


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load this custom integration during tests."""
    yield


@pytest.fixture
def fake_navilink(monkeypatch):
    """Replace the NaviLink cloud with the in-process simulator."""
    fake_cloud.FakeClient.instances.clear()
    monkeypatch.setattr(api.mqtt, "AWSIoTMQTTClient", fake_cloud.FakeClient)
    session = fake_cloud.FakeSession()
    for module in (
        "custom_components.navien_water_heater.async_get_clientsession",
        "custom_components.navien_water_heater.config_flow.async_get_clientsession",
    ):
        monkeypatch.setattr(module, lambda hass: session)
    return fake_cloud


@pytest.fixture
def config_entry(hass):
    """A configured NaviLink account entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        unique_id="user@example.com",
        title="user@example.com",
        data={"username": "user@example.com", "password": "pw"},
        options={"polling_interval": 30},
    )
    entry.add_to_hass(hass)
    return entry
