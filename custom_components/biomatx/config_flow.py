"""Config flow for BiomatX integration."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_DEVICE
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv

from .const import CONF_ALL_OFF_ADDRESS, CONF_MODULE_COUNT, CONF_SERIAL_WAIT, DOMAIN

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DEVICE): str,
        vol.Required(CONF_MODULE_COUNT): cv.positive_int,
        vol.Required(CONF_SERIAL_WAIT): cv.positive_float,
        vol.Optional(CONF_ALL_OFF_ADDRESS): cv.positive_int,
    }
)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for BiomatX."""

    VERSION = 2

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=STEP_USER_DATA_SCHEMA
            )

        return self.async_create_entry(title=DOMAIN, data=user_input)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None):
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        assert entry

        if user_input is not None:
            return self.async_update_reload_and_abort(
                    entry, data=user_input, reason="reconfigure_successful"
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE, default=entry.data[CONF_DEVICE]): str,
                    vol.Required(CONF_MODULE_COUNT, default=entry.data[CONF_MODULE_COUNT]): cv.positive_int,
                    vol.Required(CONF_SERIAL_WAIT, default=entry.data[CONF_SERIAL_WAIT]): cv.positive_float,
                    vol.Optional(CONF_ALL_OFF_ADDRESS, default=entry.data[CONF_ALL_OFF_ADDRESS]): cv.positive_int,
                }
            )
        )

class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""
