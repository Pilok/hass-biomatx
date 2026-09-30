"""Config flow for the BioMatX integration: one entry per serial device."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
)
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
    CONF_PROTOCOL,
    DOMAIN,
    MAX_MODULES,
    RELAYS_PER_MODULE,
)
from .hub import BiomatxConnectionError, BiomatxHub
from .protocol import Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

TITLE = "BioMatX"
PROBE_SECONDS = 4.0
"""
Seconds the flow listens to the bus before it proposes a module count.

A master module reports its relays every 3 s: the window holds one full period
and a second to spare. Read when the probe runs, so tests can shorten it.
"""
NOTHING_HEARD = "-"
"""Shown where the bus said nothing; the same text in every language."""


@dataclass(frozen=True, slots=True)
class BusSurvey:
    """What the bus said: its protocol, once a frame named it, and the modules heard."""

    protocol: Protocol | None = None
    modules: frozenset[int] = frozenset()
    """0-based addresses of the modules that reported their state."""

    @property
    def suggested_count(self) -> int | None:
        """
        Return the module count to propose, ``None`` when no module was heard.

        It is the highest address heard plus one, never the number of addresses
        heard: a report missed during the listen must not make the count short.
        """
        if not self.modules:
            return None
        return min(max(self.modules) + 1, MAX_MODULES)

    @property
    def placeholders(self) -> dict[str, str]:
        """Return what the modules step tells the user, modules numbered from 1."""
        return {
            "protocol": NOTHING_HEARD if self.protocol is None else self.protocol.value,
            "modules": ", ".join(str(address + 1) for address in sorted(self.modules))
            or NOTHING_HEARD,
        }


def _seen_modules(hub: BiomatxHub) -> frozenset[int]:
    """Return the addresses of the modules ``hub`` received a state report from."""
    return frozenset(
        address
        for address in range(MAX_MODULES)
        if hub.module_last_seen(address) is not None
    )


async def _async_probe(hass: HomeAssistant, device: str) -> BusSurvey | None:
    """
    Listen to the bus for ``PROBE_SECONDS`` and return what it said.

    Read only: a hub writes nothing when it connects or decodes. It is built for
    every module address, or the reports of modules beyond the count of the
    entry would be dropped as unconfigured, and without a protocol, so that the
    first valid frame names it. The modules are read before the hub is closed,
    which forgets them. Returns ``None`` when the device cannot be opened.
    """
    hub = BiomatxHub(device, MAX_MODULES, None)
    try:
        await hub.async_connect()
    except BiomatxConnectionError:
        return None
    hass.async_create_background_task(hub.async_run(), name=f"{DOMAIN} probe {device}")
    try:
        await asyncio.sleep(PROBE_SECONDS)
        return BusSurvey(hub.protocol, _seen_modules(hub))
    finally:
        await hub.async_close()


async def _async_survey_entry(
    hass: HomeAssistant, entry: ConfigEntry, device: str
) -> BusSurvey | None:
    """
    Return what the bus of a reconfigured entry says; ``None`` if it cannot be opened.

    The device of a loaded entry is not opened again: its hub is reading the bus
    and a second reader would take a share of the bytes. The hub's protocol and
    the modules it heard are read instead. Any other device is listened to. A
    silent listen on the entry's own device keeps its stored protocol (a legacy
    bus is silent by nature); on another device the stored protocol says nothing
    and is dropped.
    """
    if device != entry.data[CONF_DEVICE]:
        return await _async_probe(hass, device)
    if entry.state is ConfigEntryState.LOADED:
        hub: BiomatxHub = entry.runtime_data.hub
        return BusSurvey(hub.protocol, _seen_modules(hub))
    survey = await _async_probe(hass, device)
    stored = entry.data.get(CONF_PROTOCOL)
    if survey is None or survey.protocol is not None or stored is None:
        return survey
    return replace(survey, protocol=Protocol(stored))


def _suggest(value: Any) -> dict[str, Any]:
    """Return the field description that makes the frontend pre-fill ``value``."""
    return {} if value is None else {"suggested_value": value}


def _device_schema(device: str | None) -> vol.Schema:
    """Build the first form: the serial device only."""
    return vol.Schema(
        {
            vol.Required(CONF_DEVICE, description=_suggest(device)): TextSelector(
                TextSelectorConfig(type=TextSelectorType.TEXT)
            ),
        }
    )


def _modules_schema(count: int | None, scenario: int | None) -> vol.Schema:
    """Build the second form; ``scenario`` is the 1-based number shown to the user."""
    return vol.Schema(
        {
            vol.Required(
                CONF_MODULE_COUNT, description=_suggest(count)
            ): NumberSelector(
                NumberSelectorConfig(
                    min=1, max=MAX_MODULES, step=1, mode=NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_ALL_OFF_SCENARIO, description=_suggest(scenario)
            ): NumberSelector(
                NumberSelectorConfig(
                    min=1, max=RELAYS_PER_MODULE, step=1, mode=NumberSelectorMode.BOX
                )
            ),
        }
    )


def _entry_data(
    device: str, user_input: Mapping[str, Any], protocol: Protocol | None
) -> dict[str, Any]:
    """Turn the form values into entry data: integers, scenario stored 0-based."""
    data: dict[str, Any] = {
        CONF_DEVICE: device,
        CONF_MODULE_COUNT: int(user_input[CONF_MODULE_COUNT]),
    }
    if (scenario := user_input.get(CONF_ALL_OFF_SCENARIO)) is not None:
        data[CONF_ALL_OFF_ADDRESS] = int(scenario) - 1
    if protocol is not None:
        data[CONF_PROTOCOL] = protocol.value
    return data


def _typed_device(user_input: Mapping[str, Any]) -> str:
    return str(user_input[CONF_DEVICE]).strip()


class BiomatxConfigFlow(ConfigFlow, domain=DOMAIN):
    """
    Handle the user and reconfigure steps.

    Both ask for the device and find out what the bus says, then share the
    ``modules`` step, pre-filled from that.
    """

    VERSION = 3
    MINOR_VERSION = 1

    def __init__(self) -> None:
        """Start a flow that has heard nothing yet."""
        self._device = ""
        self._survey = BusSurvey()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the device, then listen to the bus before the modules step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            device = _typed_device(user_input)
            await self.async_set_unique_id(device)
            self._abort_if_unique_id_configured()
            if (survey := await _async_probe(self.hass, device)) is not None:
                self._device, self._survey = device, survey
                return await self.async_step_modules()
            errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="user",
            data_schema=_device_schema(user_input[CONF_DEVICE] if user_input else None),
            description_placeholders={"seconds": f"{PROBE_SECONDS:g}"},
            errors=errors,
            last_step=False,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the device in place, or keep it to review the modules."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            device = _typed_device(user_input)
            if device != entry.data[CONF_DEVICE]:
                await self.async_set_unique_id(device)
                self._abort_if_unique_id_configured()
            survey = await _async_survey_entry(self.hass, entry, device)
            if survey is not None:
                self._device, self._survey = device, survey
                return await self.async_step_modules()
            errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_device_schema(
                user_input[CONF_DEVICE] if user_input else entry.data[CONF_DEVICE]
            ),
            errors=errors,
            last_step=False,
        )

    async def async_step_modules(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """
        Ask for the number of modules and the optional all-off scenario.

        The count is pre-filled with what the bus said. A reconfiguration never
        proposes fewer modules than the entry has and keeps its scenario.
        """
        reconfiguring = self.source == SOURCE_RECONFIGURE
        if user_input is not None:
            data = _entry_data(self._device, user_input, self._survey.protocol)
            if reconfiguring:
                return self.async_update_reload_and_abort(
                    self._get_reconfigure_entry(), unique_id=self._device, data=data
                )
            return self.async_create_entry(title=TITLE, data=data)
        count, scenario = self._survey.suggested_count, None
        if reconfiguring:
            entry_data = self._get_reconfigure_entry().data
            count = max(entry_data[CONF_MODULE_COUNT], count or 0)
            if (address := entry_data.get(CONF_ALL_OFF_ADDRESS)) is not None:
                scenario = address + 1
        return self.async_show_form(
            step_id="modules",
            data_schema=_modules_schema(count, scenario),
            description_placeholders=self._survey.placeholders,
            last_step=True,
        )
