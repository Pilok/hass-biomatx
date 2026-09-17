"""Constants for the BioMatX integration."""

from typing import Final

from .protocol.model import BUTTONS_PER_MODULE

DOMAIN: Final = "biomatx"

EVENT_INVALID_FRAME: Final = f"{DOMAIN}_invalid_frame"
"""
Fired on the Home Assistant event bus for every checksummed frame the format
cannot carry (the detectors' "module 4, output 11" frame). Data: ``entry_id``,
``raw`` (hex), ``reason``, ``target_module``, ``emitter_module``, ``output``
(1-based, ``None`` when unreadable), ``pressed``.
"""

CONF_MODULE_COUNT: Final = "module_count"
CONF_ALL_OFF_ADDRESS: Final = "all_off_address"
"""Stored 0-based address of the all-off scenario button, absent when unset."""
CONF_ALL_OFF_SCENARIO: Final = "all_off_scenario"
"""Form field: the same scenario as a 1-based number, like the manual."""
CONF_PROTOCOL: Final = "protocol"
"""Stored ``Protocol`` value (``legacy`` | ``master``); absent = detect on the bus."""
CONF_SERIAL_WAIT: Final = "serial_wait"
"""Legacy upstream field with no effect; removed by the entry migration."""

MANUFACTURER: Final = "PSO"
MODEL_MODULE: Final = "BioMatX 2110"
MODEL_BUS: Final = "RS485 bus"
MODEL_SCENARIOS: Final = "Scenario module"

MAX_MODULES: Final = 7
"""Module addresses 1-7 on the front panel; address 8 is the scenario module."""
RELAYS_PER_MODULE: Final = BUTTONS_PER_MODULE
