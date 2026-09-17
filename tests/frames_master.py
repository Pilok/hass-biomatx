"""
Frames of the "master" firmware captured on real buses on 2026-09-14.

Every frame starts with ``a5``; byte 2 is the XOR checksum (the XOR of the whole
frame is zero). State frames are 9 bytes (``a5 <xor> 7f <0x40|module> 81 01 00
<relays 1-8> <relays 9-10>``), event frames 6 bytes (``a5 <xor> <target>
<0x80|emitter> 84 <0x40|button pressed, button released>``). Module and relay
numbers in the names are 1-based like the front panels; the frames are 0-based.
"""

from __future__ import annotations

from pathlib import Path
import re

FIXTURES = Path(__file__).parent / "fixtures"
HALL_CAPTURE = FIXTURES / "2026-09-14-enersol-hall.log"

# --- state frames, Enersol hall (two modules) -----------------------------------
STATE_M1_ALL_OFF = "a5 1a 7f 40 81 01 00 00 00"
STATE_M2_ALL_OFF = "a5 1b 7f 41 81 01 00 00 00"
STATE_M1_R1_ON = "a5 1b 7f 40 81 01 00 01 00"
STATE_M1_R2_R9_ON = "a5 19 7f 40 81 01 00 02 01"
STATE_M1_R5_ON = "a5 0a 7f 40 81 01 00 10 00"
STATE_M2_R1_ON = "a5 1a 7f 41 81 01 00 01 00"

# --- state frames, Benoit's house (four modules), 2026-09-14 08:40 ---------------
STATE_HOUSE_M1 = "a5 18 7f 40 81 01 00 02 00"  # relay 2 on
STATE_HOUSE_M2 = "a5 5f 7f 41 81 01 00 44 00"  # relays 3 and 7 on
STATE_HOUSE_M3 = "a5 1e 7f 42 81 01 00 06 00"  # relays 2 and 3 on
STATE_HOUSE_M4 = "a5 da 7f 43 81 01 00 c0 03"  # relays 7, 8, 9 and 10 on

# --- event frames -------------------------------------------------------------------
PRESS_M1_R1 = "a5 e1 00 80 84 40"  # front panel, emitter == target
RELEASE_M1_R1 = "a5 a1 00 80 84 00"
PRESS_M1_R2 = "a5 e0 00 80 84 41"
RELEASE_M1_R2 = "a5 a0 00 80 84 01"
PRESS_M1_R9 = "a5 e9 00 80 84 48"
RELEASE_M1_R9 = "a5 a9 00 80 84 08"
PRESS_M2_R1 = "a5 e1 01 81 84 40"
RELEASE_M2_R1 = "a5 a1 01 81 84 00"

# Wall switch wired on module 2, driving module 1 relay 5 (test T4).
WALL_PRESS_M2_TO_M1_R5 = "a5 e4 00 81 84 44"
WALL_RELEASE_M2_TO_M1_R5 = "a5 a4 00 81 84 04"

# Wall scenario button (module 2) firing scenario 1 on the scenario module (7).
SCENARIO_1_PRESS = "a5 e7 07 81 84 40"
SCENARIO_1_RELEASE = "a5 a7 07 81 84 00"

# --- corrupted or foreign input -------------------------------------------------------
STATE_M1_BAD_CHECKSUM = "a5 1a 7f 40 81 01 00 01 00"  # payload of R1_ON, xor of ALL_OFF
EVENT_BAD_CHECKSUM = "a5 e1 00 80 84 41"
UNKNOWN_TYPE = "a5 00 7f 40 99 01 00 00 00"
ORPHAN_START = "a5"
TRUNCATED_STATE = "a5 1b 7f 41 81 01 00 00"  # last byte lost on the wire
# Valid checksum, button index 15: a collision leftover that the format cannot carry.
EVENT_INVALID_BUTTON = "a5 ee 00 80 84 4f"
# Captured on the owner's four-module bus on 2026-09-15 and 2026-09-16, each within
# 90 ms of a detector press: valid checksum, target module 4 (index 3), "output 11"
# (button index 10) which no module has. Enersol: a virtual relay the detectors use
# to coordinate; the master firmware acts on it as a press on relay 1 of module 4.
PHANTOM_PRESS_M4_OUT11 = "a5 e8 03 80 84 4a"  # emitted by module 1
PHANTOM_RELEASE_M4_OUT11 = "a5 a8 03 80 84 0a"
PHANTOM_PRESS_M4_OUT11_FROM_M2 = "a5 e9 03 81 84 4a"  # emitted by module 2
# Valid checksum, module field without the 0x40 flag.
STATE_INVALID_MODULE = "a5 5a 7f 00 81 01 00 00 00"
# A state frame whose relay byte happens to be a5 (relays 1, 3, 6, 8 on).
STATE_M1_RELAYS_A5 = "a5 bf 7f 40 81 01 00 a5 00"
# A state frame whose checksum byte happens to be a5 (relays 3-6, 8-10 on).
STATE_M1_CHECKSUM_A5 = "a5 a5 7f 40 81 01 00 bc 03"
# Valid checksum, bits set in the second relay byte beyond relays 9 and 10.
STATE_PHANTOM_RELAYS = "a5 e5 7f 40 81 01 00 00 ff"
# Valid checksum, state report from the virtual scenario module (never emitted).
STATE_SCENARIO_MODULE = "a5 1d 7f 47 81 01 00 00 00"
# Legacy two-byte frames, as an old-firmware bus would emit them.
LEGACY_PRESS_M1_R1 = "50 00"
LEGACY_RELEASE_M1_R1 = "50 80"

_PACKET_LINE = re.compile(r" n=(?P<count>\d+)\s+(?P<hex>(?:[0-9a-f]{2} ?)+)\s*\?\s*$")


def hall_capture() -> bytes:
    """
    Return every byte captured in the hall on 2026-09-14, in order.

    The capture tool wrote one line per burst separated by silence, plus markers
    and its own status lines; only the hex payload of the burst lines is kept.
    Between 08:01:56 and 08:02:44 two capture processes shared the port (see the
    marker at 08:02:44 in the log): the bytes of that window are split across
    odd-sized lines but concatenate back into complete, valid frames.
    """
    stream = bytearray()
    for line in HALL_CAPTURE.read_text().splitlines():
        match = _PACKET_LINE.search(line)
        if match is None:
            continue
        payload = bytes.fromhex(match["hex"])
        assert len(payload) == int(match["count"]), line
        stream += payload
    return bytes(stream)
