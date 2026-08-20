"""Config flow for the Navien NaviLink Water Heater integration."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    AWS_CERT_FILE,
    CONF_PASSWORD,
    CONF_POLLING_INTERVAL,
    CONF_USERNAME,
    DEFAULT_POLLING_INTERVAL,
    DOMAIN,
    MAX_POLLING_INTERVAL,
    MIN_POLLING_INTERVAL,
)
from .navien_api import (
    NavienAccount,
    NavienAuthError,
    NavienConnectionError,
    NavienNoDevicesError,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(
            CONF_POLLING_INTERVAL, default=DEFAULT_POLLING_INTERVAL
        ): vol.All(
            vol.Coerce(int), vol.Range(min=MIN_POLLING_INTERVAL, max=MAX_POLLING_INTERVAL)
        ),
    }
)

STEP_REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})


class NavienConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for a NaviLink account."""

    VERSION = 2

    def __init__(self) -> None:
        """Initialise the flow."""
        self._reauth_entry: ConfigEntry | None = None

    async def _async_validate(self, username: str, password: str) -> tuple[int, str | None]:
        """Try to sign in; return the number of gateways and an error key."""
        account = NavienAccount(
            username=username,
            password=password,
            session=async_get_clientsession(self.hass),
            aws_cert_path=os.path.join(os.path.dirname(__file__), "cert", AWS_CERT_FILE),
            polling_interval=0,
        )
        try:
            devices = await account.async_login()
        except NavienAuthError:
            return 0, "invalid_auth"
        except NavienNoDevicesError:
            return 0, "no_devices"
        except NavienConnectionError:
            return 0, "cannot_connect"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error while validating NaviLink credentials")
            return 0, "unknown"
        return len(devices), None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the NaviLink account credentials."""
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            await self.async_set_unique_id(username.lower())
            self._abort_if_unique_id_configured()

            count, error = await self._async_validate(username, user_input[CONF_PASSWORD])
            if error == "no_devices":
                return self.async_abort(reason="no_devices")
            if error:
                errors["base"] = error
            else:
                _LOGGER.debug("NaviLink account %s holds %s gateway(s)", username, count)
                return self.async_create_entry(
                    title=username,
                    data={
                        CONF_USERNAME: username,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                    options={
                        CONF_POLLING_INTERVAL: user_input.get(
                            CONF_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL
                        )
                    },
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle credentials that stopped working."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new password for an existing account."""
        errors: dict[str, str] = {}
        entry = self._reauth_entry

        if entry is None:
            return self.async_abort(reason="reauth_failed")

        if user_input is not None:
            username = entry.data[CONF_USERNAME]
            _, error = await self._async_validate(username, user_input[CONF_PASSWORD])
            if error and error != "no_devices":
                errors["base"] = error
            else:
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            description_placeholders={"username": entry.data.get(CONF_USERNAME, "")},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> NavienOptionsFlow:
        """Return the options flow for this integration."""
        return NavienOptionsFlow()


class NavienOptionsFlow(OptionsFlow):
    """Let the user retune an existing NaviLink account."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the polling interval."""
        entry = self.hass.config_entries.async_get_entry(self.handler)
        if entry is None:  # pragma: no cover - defensive
            return self.async_abort(reason="unknown")

        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = entry.options.get(
            CONF_POLLING_INTERVAL,
            entry.data.get(CONF_POLLING_INTERVAL, DEFAULT_POLLING_INTERVAL),
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_POLLING_INTERVAL, default=current): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_POLLING_INTERVAL, max=MAX_POLLING_INTERVAL),
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
