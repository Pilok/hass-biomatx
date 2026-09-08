"""Config flow for the BioMatX integration: one entry per serial device."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_DEVICE
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .const import (
    CONF_ALL_OFF_ADDRESS,
    CONF_ALL_OFF_SCENARIO,
    CONF_MODULE_COUNT,
    DOMAIN,
    MAX_MODULES,
    RELAYS_PER_MODULE,
)
from .hub import BiomatxConnectionError, BiomatxHub

if TYPE_CHECKING:
    from collections.abc import Mapping

TITLE = "BioMatX"


def _form_schema(suggested: Mapping[str, Any]) -> vol.Schema:
    """Build the form; ``suggested`` pre-fills it (1-based scenario number)."""

    def _suggest(key: str) -> dict[str, Any]:
        value = suggested.get(key)
        return {"suggested_value": value} if value is not None else {}

    return vol.Schema(
        {
            vol.Required(CONF_DEVICE, description=_suggest(CONF_DEVICE)): TextSelector(
                TextSelectorConfig(type=TextSelectorType.TEXT)
            ),
            vol.Required(
                CONF_MODULE_COUNT, description=_suggest(CONF_MODULE_COUNT)
            ): NumberSelector(
                NumberSelectorConfig(
                    min=1, max=MAX_MODULES, step=1, mode=NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_ALL_OFF_SCENARIO, description=_suggest(CONF_ALL_OFF_SCENARIO)
            ): NumberSelector(
                NumberSelectorConfig(
                    min=1, max=RELAYS_PER_MODULE, step=1, mode=NumberSelectorMode.BOX
                )
            ),
        }
    )


def _entry_data(user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Turn form values into entry data: integers, scenario stored 0-based."""
    data: dict[str, Any] = {
        CONF_DEVICE: str(user_input[CONF_DEVICE]).strip(),
        CONF_MODULE_COUNT: int(user_input[CONF_MODULE_COUNT]),
    }
    if (scenario := user_input.get(CONF_ALL_OFF_SCENARIO)) is not None:
        data[CONF_ALL_OFF_ADDRESS] = int(scenario) - 1
    return data


def _form_values(data: Mapping[str, Any]) -> dict[str, Any]:
    """Turn entry data back into form values (scenario shown 1-based)."""
    values = {
        CONF_DEVICE: data[CONF_DEVICE],
        CONF_MODULE_COUNT: data[CONF_MODULE_COUNT],
    }
    if (address := data.get(CONF_ALL_OFF_ADDRESS)) is not None:
        values[CONF_ALL_OFF_SCENARIO] = address + 1
    return values


async def _async_can_open(device: str) -> bool:
    """Open and close the device once; nothing is written on the bus."""
    hub = BiomatxHub(device, 1, None)
    try:
        await hub.async_connect()
    except BiomatxConnectionError:
        return False
    await hub.async_close()
    return True


class BiomatxConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the user and reconfigure steps."""

    VERSION = 2
    MINOR_VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the device, the module count and the optional all-off scenario."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _entry_data(user_input)
            await self.async_set_unique_id(data[CONF_DEVICE])
            self._abort_if_unique_id_configured()
            if await _async_can_open(data[CONF_DEVICE]):
                return self.async_create_entry(title=TITLE, data=data)
            errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="user",
            data_schema=_form_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the device, the module count or the all-off scenario in place."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _entry_data(user_input)
            device = data[CONF_DEVICE]
            if device != entry.data[CONF_DEVICE]:
                await self.async_set_unique_id(device)
                self._abort_if_unique_id_configured()
            if await _async_can_open(device):
                return self.async_update_reload_and_abort(
                    entry, unique_id=device, data=data
                )
            errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_form_schema(user_input or _form_values(entry.data)),
            errors=errors,
        )
