"""The BioMatX integration: BioMatX 2110 lighting modules over their RS485 bus."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_DEVICE, Platform
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)

from .const import (
    CONF_ALL_OFF_ADDRESS,
    CONF_MODULE_COUNT,
    CONF_SERIAL_WAIT,
    DOMAIN,
    MANUFACTURER,
    MODEL_BUS,
)
from .hub import BiomatxConnectionError, BiomatxHub

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.typing import ConfigType

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.LIGHT]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
CURRENT_ENTRY_VERSION = 2


@dataclass(slots=True)
class BiomatxData:
    """Runtime data of a config entry."""

    hub: BiomatxHub
    hub_device_id: str


type BiomatxConfigEntry = ConfigEntry[BiomatxData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:  # noqa: ARG001  # HA signature
    """Set up the integration (nothing to do before a config entry exists)."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: BiomatxConfigEntry) -> bool:
    """Open the bus, start reading it and set up the platforms."""
    device: str = entry.data[CONF_DEVICE]
    hub = BiomatxHub(
        device, entry.data[CONF_MODULE_COUNT], entry.data.get(CONF_ALL_OFF_ADDRESS)
    )
    try:
        await hub.async_connect()
    except BiomatxConnectionError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"device": device, "error": str(err)},
        ) from err

    hub_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer=MANUFACTURER,
        model=MODEL_BUS,
        name="BioMatX bus",
    )
    entry.runtime_data = BiomatxData(hub=hub, hub_device_id=hub_device.id)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Start reading only once the entities exist: frames received meanwhile stay
    # buffered in the transport and are applied to entities that can show them.
    entry.async_create_background_task(
        hass, hub.async_run(), name=f"{DOMAIN} reader {entry.entry_id}"
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BiomatxConfigEntry) -> bool:
    """Unload the platforms, then close the bus if they all went away."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.hub.async_close()
    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Bring a config entry written by an older release up to date.

    Home Assistant refuses entries newer than CURRENT_ENTRY_VERSION by itself.
    """
    if entry.version == 1:
        data = {
            key: value for key, value in entry.data.items() if key != CONF_SERIAL_WAIT
        }
        await _async_migrate_legacy_registries(hass, entry)
        hass.config_entries.async_update_entry(
            entry,
            data=data,
            version=CURRENT_ENTRY_VERSION,
            unique_id=entry.unique_id or data[CONF_DEVICE],
        )
        _LOGGER.info(
            "Migrated config entry %s to version %s", entry.title, entry.version
        )
    return True


LEGACY_UNIQUE_ID = re.compile(r"^(?P<module>[0-7])_(?P<switch>[0-9])$")
LEGACY_KINDS = {Platform.LIGHT: "relay", Platform.BINARY_SENSOR: "switch"}


async def _async_migrate_legacy_registries(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """
    Rename the upstream unique ids (``"1_7"``) so entity ids survive the upgrade.

    Upstream also created one device per relay; those devices are removed, the
    entities move to the module devices when they are set up again.
    """

    @callback
    def _migrate(entity_entry: er.RegistryEntry) -> dict[str, str] | None:
        match = LEGACY_UNIQUE_ID.match(entity_entry.unique_id)
        kind = LEGACY_KINDS.get(entity_entry.domain)
        if match is None or kind is None:
            return None
        new_unique_id = f"{entry.entry_id}-{kind}-{match['module']}-{match['switch']}"
        _LOGGER.info(
            "Migrating %s from unique id %s to %s",
            entity_entry.entity_id,
            entity_entry.unique_id,
            new_unique_id,
        )
        return {"new_unique_id": new_unique_id}

    await er.async_migrate_entries(hass, entry.entry_id, _migrate)
    device_registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        if any(
            domain == DOMAIN and LEGACY_UNIQUE_ID.match(identifier)
            for domain, identifier in device.identifiers
        ):
            device_registry.async_remove_device(device.id)
