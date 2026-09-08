"""
Frames captured on the real bus on 2026-09-08, as named test data.

Byte 1 = ``0x5e`` (``e`` = emitting module), byte 2 = released bit, target module,
target relay. Module and relay numbers in the names are 1-based like the
front panels; the frames themselves are 0-based.
"""

PRESS_M1_R1 = "50 00"
RELEASE_M1_R1 = "50 80"
PRESS_M1_R8 = "50 07"
RELEASE_M1_R8 = "50 87"
PRESS_M2_R8 = "51 17"
RELEASE_M2_R8 = "51 97"
PRESS_M4_R10 = "53 39"
RELEASE_M4_R10 = "53 b9"

# Corridor motion detector: emitted by module 2, targets module 1 relay 8.
DETECTOR_CORRIDOR_PRESS = "51 07"
DETECTOR_CORRIDOR_RELEASE = "51 87"

# Bus collisions between two detectors.
COLLISION_INVALID_SWITCH = "50 3a"  # switch index 10 does not exist
COLLISION_WRONG_RELEASE = "50 96"  # release for relay 7 instead of relay 10

# A lone start byte whose second byte never came.
ORPHAN_START_BYTE = "50"

# Scenario module (address 7): scenario 6 pressed then released. Byte 2 carries
# the target module (7) like any frame, so 0x75 = module 7, button 5.
SCENARIO_6_PRESS = "57 75"
SCENARIO_6_RELEASE = "57 f5"

# Module 7 is the scenario module; module 6 exists nowhere on a 4-module bus.
UNCONFIGURED_MODULE_PRESS = "56 60"

# The library also accepts frames whose start nibble is 0xA.
ALT_START_NIBBLE_PRESS = "a0 00"
