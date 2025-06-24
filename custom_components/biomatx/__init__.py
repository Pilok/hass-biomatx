"""The BiomatX integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_DEVICE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .bus import BiomatxBus
from .const import _LOGGER, CONF_ALL_OFF_ADDRESS, CONF_MODULE_COUNT, CONF_SERIAL_WAIT, DOMAIN

PLATFORMS: list[str] = ["binary_sensor", "light"]

async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old entry."""
    _LOGGER.debug("migrating from version %s", entry.version)

    if entry.version == 1:
        new = {**entry.data}
        new[CONF_SERIAL_WAIT] = 0.5

        entry.version = 2
        hass.config_entries.async_update_entry(entry, data=new)

    _LOGGER.info("Migration to version %s successful", entry.version)

    return True

async def async_setup(hass: HomeAssistant, config: ConfigType):
    """Setups the BiomatX integration."""
    hass.data[DOMAIN] = {}
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up BiomatX from a config entry."""
    _LOGGER.debug("setting up BiomatX bus, config entry version %s", entry.version)

    bus = BiomatxBus(
        hass, entry.data[CONF_MODULE_COUNT], entry.data[CONF_SERIAL_WAIT], entry.data.get(CONF_ALL_OFF_ADDRESS, None)
    )
    hass.data[DOMAIN][entry.entry_id] = bus

    await bus.connect(entry.data[CONF_DEVICE])

    for platform in PLATFORMS:
        hass.async_create_task(
            hass.config_entries.async_forward_entry_setups(entry, [platform])
        )

    hass.services.async_register(DOMAIN, "reset", bus.reset)
    hass.services.async_register(DOMAIN, "all_off", bus.all_off)
    hass.services.async_register(DOMAIN, "reload", bus.reload)
    hass.loop.create_task(bus.loop())
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    bus = hass.data[DOMAIN].pop(entry.entry_id)
    await bus.stop()
    for platform in PLATFORMS:
        hass.async_create_task(
            hass.config_entries.async_forward_entry_unload(entry, platform)
        )

    return True

