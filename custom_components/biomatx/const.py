"""Constants for the BioMatX integration."""

from typing import Final

DOMAIN: Final = "biomatx"

CONF_MODULE_COUNT: Final = "module_count"
CONF_ALL_OFF_ADDRESS: Final = "all_off_address"
"""Stored 0-based address of the all-off scenario button, absent when unset."""
CONF_SERIAL_WAIT: Final = "serial_wait"
"""Legacy upstream field with no effect; removed by the entry migration."""

MANUFACTURER: Final = "PSO"
MODEL_MODULE: Final = "BioMatX 2110"
MODEL_BUS: Final = "RS485 bus"
MODEL_SCENARIOS: Final = "Scenario module"
