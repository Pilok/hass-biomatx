"""Base entity shared by the BioMatX platforms."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from biomatx import SCENARIO_MODULE_ADDRESS

from .const import DOMAIN, MANUFACTURER, MODEL_MODULE, MODEL_SCENARIOS

if TYPE_CHECKING:
    import biomatx

    from . import BiomatxConfigEntry
    from .hub import BiomatxHub, DeviceKey, DeviceKind


def module_device_info(
    entry_id: str, module_address: int, via_device_id: str
) -> DeviceInfo:
    """Describe the device of one module, linked to the hub device of the bus."""
    if module_address == SCENARIO_MODULE_ADDRESS:
        name, model = "BioMatX scenarios", MODEL_SCENARIOS
    else:
        name, model = f"BioMatX module {module_address + 1}", MODEL_MODULE
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry_id}-module-{module_address}")},
        name=name,
        manufacturer=MANUFACTURER,
        model=model,
        via_device_id=via_device_id,
    )


class BiomatxEntity(Entity):
    """An entity bound to one relay or button of one module."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        entry: BiomatxConfigEntry,
        module: biomatx.Module,
        address: int,
        kind: DeviceKind,
    ) -> None:
        """Bind to the hub of ``entry`` and to device ``(kind, module, address)``."""
        self._hub: BiomatxHub = entry.runtime_data.hub
        self._key: DeviceKey = (kind, module.address, address)
        self._attr_unique_id = f"{entry.entry_id}-{kind}-{module.address}-{address}"
        self._attr_device_info = module_device_info(
            entry.entry_id, module.address, entry.runtime_data.hub_device_id
        )
        self._attr_translation_placeholders = {"number": str(address + 1)}

    @property
    def available(self) -> bool:
        """Return whether the serial link to the bus is up."""
        return self._hub.connected

    async def async_added_to_hass(self) -> None:
        """Follow the device and the link once the entity is registered."""
        await super().async_added_to_hass()
        self.async_on_remove(self._hub.add_listener(self._key, self._handle_update))
        self.async_on_remove(
            self._hub.add_link_listener(lambda _connected: self._handle_update())
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()
