"""Event platform: one event entity per button and per scenario."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import callback

from .entity import BiomatxEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from . import BiomatxConfigEntry
    from .protocol.model import Switch

PARALLEL_UPDATES = 0
EVENT_PRESSED = "pressed"
EVENT_RELEASED = "released"
ATTR_EMITTER_MODULE = "emitter_module"


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001  # HA signature
    entry: BiomatxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one event entity per button, scenario buttons included."""
    async_add_entities(
        BiomatxButtonEvent(entry, switch) for switch in entry.runtime_data.hub.switches
    )


class BiomatxButtonEvent(BiomatxEntity, EventEntity):
    """
    A button of a module, or a scenario button of the virtual module.

    Fires ``pressed`` and ``released`` with the 1-based module the physical
    button or detector is wired on (``emitter_module``): a detector wired on
    module 2 that drives module 1 relay 8 fires on module 1 button 8 with
    ``emitter_module`` 2.
    """

    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types: ClassVar[list[str]] = [EVENT_PRESSED, EVENT_RELEASED]

    @property
    def available(self) -> bool:
        """
        Return whether the link is up.

        An event is a fact carried by the bus at a point in time, whether or
        not the target module is reporting its state; only the link matters.
        """
        return self._hub.connected

    def __init__(self, entry: BiomatxConfigEntry, switch: Switch) -> None:
        """Bind the entity to ``switch``."""
        super().__init__(entry, switch.module, switch.address, "switch")
        self._switch = switch
        self._seen_events = switch.events
        self._attr_translation_key = (
            "scenario" if switch.module.is_scenario else "button"
        )

    @callback
    def _handle_update(self) -> None:
        """Fire an event when the button moved; a mere refresh only rewrites state."""
        if self._switch.events != self._seen_events:
            self._seen_events = self._switch.events
            self._trigger_event(
                EVENT_PRESSED if self._switch.pressed else EVENT_RELEASED,
                {ATTR_EMITTER_MODULE: self._switch.emitter + 1},
            )
        self.async_write_ha_state()
