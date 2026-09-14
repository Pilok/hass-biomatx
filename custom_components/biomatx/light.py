"""Light platform: one light per BioMatX relay."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .entity import BiomatxEntity
from .hub import BiomatxCommandError, BiomatxLinkError, BiomatxModuleUnavailableError

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import BiomatxConfigEntry
    from .protocol.model import Relay

# Commands are serialised by the hub's own lock; no platform-level limit needed.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001  # HA signature
    entry: BiomatxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one light per relay of the configured modules."""
    async_add_entities(
        BiomatxLight(entry, relay) for relay in entry.runtime_data.hub.relays
    )


class BiomatxLight(BiomatxEntity, LightEntity, RestoreEntity):
    """
    A relay driven by simulated button presses.

    On the master firmware the state is the one the module reports. On the
    legacy firmware it is inferred from the presses seen and sent, declared as
    assumed, and restored across restarts because the bus cannot tell it.
    ``RestoreEntity`` is a base class for that legacy path only.
    """

    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = frozenset({ColorMode.ONOFF})
    _attr_translation_key = "relay"

    def __init__(self, entry: BiomatxConfigEntry, relay: Relay) -> None:
        """Bind the light to ``relay``."""
        super().__init__(entry, relay.module, relay.address, "relay")
        self._relay = relay

    @property
    def is_on(self) -> bool:
        """Return the relay state, reported (master) or inferred (legacy)."""
        return self._relay.on

    @property
    def assumed_state(self) -> bool:
        """Return whether the state is inferred rather than reported by the module."""
        return not self._hub.reports_state

    async def async_added_to_hass(self) -> None:
        """
        Restore the last known state unless the modules report theirs.

        A bus whose protocol is not detected yet is treated as legacy: a legacy
        bus stays silent until someone presses a button, so waiting would lose
        the only chance to restore. If the bus turns out to be master, the
        detection marks every module unavailable until its first report.
        """
        if not self._hub.reports_state:
            last_state = await self.async_get_last_state()
            if last_state is not None and last_state.state in (STATE_ON, STATE_OFF):
                self._relay.on = last_state.state == STATE_ON
        await super().async_added_to_hass()

    async def async_turn_on(self, **kwargs: Any) -> None:  # noqa: ARG002  # HA signature
        """Press the button unless the relay is already on."""
        await self._async_set(on=True)

    async def async_turn_off(self, **kwargs: Any) -> None:  # noqa: ARG002  # HA signature
        """Press the button unless the relay is already off."""
        await self._async_set(on=False)

    async def _async_set(self, *, on: bool) -> None:
        try:
            await self._hub.async_set_relay(self._relay, on=on)
        except BiomatxLinkError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="link_down"
            ) from err
        except BiomatxModuleUnavailableError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="module_unavailable",
                translation_placeholders={
                    "module": str(self._relay.module.address + 1)
                },
            ) from err
        except BiomatxCommandError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="not_confirmed",
                translation_placeholders={
                    "module": str(self._relay.module.address + 1)
                },
            ) from err
