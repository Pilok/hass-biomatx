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
    CONF_PROTOCOL,
    CONF_SERIAL_WAIT,
    DOMAIN,
    EVENT_INVALID_FRAME,
    MANUFACTURER,
    MODEL_BUS,
)
from .hub import BiomatxConnectionError, BiomatxHub
from .protocol import InvalidFrame, Protocol

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.typing import ConfigType

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.EVENT, Platform.LIGHT]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
CURRENT_ENTRY_VERSION = 3
"""Version the config flow writes and the last migration step ends on."""


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
    stored_protocol = entry.data.get(CONF_PROTOCOL)
    hub = BiomatxHub(
        device,
        entry.data[CONF_MODULE_COUNT],
        entry.data.get(CONF_ALL_OFF_ADDRESS),
        protocol=None if stored_protocol is None else Protocol(stored_protocol),
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

    @callback
    def _fire_invalid_frame(frame: InvalidFrame) -> None:
        """Expose a frame the format cannot carry to automations, 1-based."""
        hass.bus.async_fire(
            EVENT_INVALID_FRAME,
            {
                "entry_id": entry.entry_id,
                "raw": frame.raw.hex(" "),
                "reason": str(frame.reason),
                "target_module": _one_based(frame.target),
                "emitter_module": _one_based(frame.emitter),
                "output": _one_based(frame.button),
                "pressed": frame.pressed,
            },
        )

    entry.async_on_unload(hub.add_invalid_frame_listener(_fire_invalid_frame))
    if stored_protocol is None:

        @callback
        def _store_protocol(protocol: Protocol) -> None:
            """Remember the detected protocol so the next start skips detection."""
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_PROTOCOL: protocol.value}
            )

        entry.async_on_unload(hub.add_protocol_listener(_store_protocol))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Start reading only once the entities exist: frames received meanwhile stay
    # buffered in the transport and are applied to entities that can show them.
    entry.async_create_background_task(
        hass, hub.async_run(), name=f"{DOMAIN} reader {entry.entry_id}"
    )
    return True


def _one_based(address: int | None) -> int | None:
    """Turn a 0-based bus address into the number printed on the front panels."""
    return None if address is None else address + 1


async def async_unload_entry(hass: HomeAssistant, entry: BiomatxConfigEntry) -> bool:
    """Unload the platforms, then close the bus if they all went away."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.hub.async_close()
    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """
    Bring a config entry written by an older release up to date, one version at a time.

    ``_MIGRATIONS[n]`` takes an entry from version ``n`` to ``n + 1``. Home
    Assistant refuses entries newer than CURRENT_ENTRY_VERSION by itself.
    """
    for version in range(entry.version, CURRENT_ENTRY_VERSION):
        await _MIGRATIONS[version](hass, entry)
    return True


async def _async_migrate_to_version_2(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Drop the inert ``serial_wait``, set the unique id, keep the entity ids."""
    data = {key: value for key, value in entry.data.items() if key != CONF_SERIAL_WAIT}
    await _async_migrate_legacy_registries(hass, entry)
    hass.config_entries.async_update_entry(
        entry,
        data=data,
        version=2,
        unique_id=entry.unique_id or data[CONF_DEVICE],
    )
    _LOGGER.info("Migrated config entry %s to version %s", entry.title, entry.version)


async def _async_migrate_to_version_3(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """
    Remove the ``binary_sensor`` entries of the entry from the entity registry.

    The upstream integration made one per button. Buttons are ``event`` entities
    now, with the same unique ids, and a registry entry is keyed by domain as
    well: the old ones stay behind, unavailable for good. The entry's data is
    left as it is.
    """
    registry = er.async_get(hass)
    for entity_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            entity_entry.domain == Platform.BINARY_SENSOR
            and entity_entry.platform == DOMAIN
        ):
            _LOGGER.info(
                "Removing %s: the integration has no binary_sensor platform any more",
                entity_entry.entity_id,
            )
            registry.async_remove(entity_entry.entity_id)
    hass.config_entries.async_update_entry(entry, version=3)
    _LOGGER.info("Migrated config entry %s to version %s", entry.title, entry.version)


_MIGRATIONS: dict[int, Callable[[HomeAssistant, ConfigEntry], Awaitable[None]]] = {
    1: _async_migrate_to_version_2,
    2: _async_migrate_to_version_3,
}


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
