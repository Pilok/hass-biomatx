"""Light platform: one assumed-state light per BioMatX relay."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .entity import BiomatxEntity
from .hub import BiomatxLinkError

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    import biomatx

    from . import BiomatxConfigEntry

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
    """A relay driven by simulated button presses; its state is inferred."""

    _attr_assumed_state = True
    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = frozenset({ColorMode.ONOFF})
    _attr_translation_key = "relay"

    def __init__(self, entry: BiomatxConfigEntry, relay: biomatx.Relay) -> None:
        """Bind the light to ``relay``."""
        super().__init__(entry, relay.module, relay.address, "relay")
        self._relay = relay

    @property
    def is_on(self) -> bool:
        """Return the inferred relay state."""
        return self._relay.on

    async def async_added_to_hass(self) -> None:
        """Restore the last known state before following the bus: it cannot tell us."""
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in (STATE_ON, STATE_OFF):
            self._relay.on = last_state.state == STATE_ON
        await super().async_added_to_hass()

    async def async_turn_on(self, **kwargs: Any) -> None:  # noqa: ARG002  # HA signature
        """Press the button unless the relay is already believed on."""
        await self._async_set(on=True)

    async def async_turn_off(self, **kwargs: Any) -> None:  # noqa: ARG002  # HA signature
        """Press the button unless the relay is already believed off."""
        await self._async_set(on=False)

    async def _async_set(self, *, on: bool) -> None:
        try:
            await self._hub.async_set_relay(self._relay, on=on)
        except BiomatxLinkError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="link_down"
            ) from err
