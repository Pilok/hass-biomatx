# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `hub.py` on the `protocol/` codecs. **Master firmware**: relay state comes
  from the state reports only (a report updates the relays that changed and
  wakes their listeners), a command sends press then release and waits up to
  2 s for the report that confirms the wanted state (`BiomatxCommandError`
  otherwise, never an optimistic flip), `turn_on` of a relay already reported
  on sends nothing, a module silent for 10 s is unavailable
  (`module_available`, `module_last_seen`) and its entities are woken on each
  change, an event only updates the button, the all-off scenario observed or
  sent leaves the relays to the modules' own reports, scenario commands are
  emitted as module 1, `reset` is refused (`BiomatxNotSupportedError`).
  **Legacy firmware**: behaviour unchanged (inferred state, optimistic flip,
  `reset`), pinned by the same 46 tests as before. **Protocol detection**:
  an entry without a stored `protocol` listens to the bus and picks the codec
  from the first valid frame, replaying the bytes seen meanwhile; a command
  before detection is refused (`BiomatxProtocolUnknownError`). Codec counters
  exposed as `hub.stats`; the codec is reset on every reconnection so a frame
  cut by an outage is not glued to the new link.
- Config entry key `protocol` (`legacy` | `master`), optional, read at setup;
  the config flow does not write it yet.

### Changed

- Serial transport on `serialx` 1.10.0, the library Home Assistant core ships,
  with exclusive access to the device; `pyserial-asyncio` is no longer used.

### Removed

- The `biomatx` package: its data model lives in `protocol/model.py` since the
  previous change, and nothing imports it any more.

- `protocol/` package, the bus protocols without any Home Assistant, serial or
  `biomatx` import: `Protocol` (`legacy` | `master`), the frame types
  `EventFrame` (target, emitter, button, pressed) and `StateFrame` (module,
  relay bitmap), the data model of an installation ported from `pybiomatx`
  (`Installation`, `Module`, `Relay`, `Switch`), the legacy two-byte codec
  moved out of `hub.py` unchanged, the codec of the "master" firmware
  (`a5` start byte, XOR checksum, 9-byte state reports, 6-byte events,
  byte-by-byte parser that resynchronises on the next start byte after a bad
  checksum or an unknown type, field validation of frames that pass the
  checksum, `reset()` for reconnections, counters for diagnostics), and
  `detect()` which
  names the protocol from a sample of traffic (master first: its frames carry
  a checksum, and a master frame can pass for a legacy one). The whole Enersol
  showroom capture of 2026-09-14 (653 state reports, 22 events) is replayed in
  the tests at every read boundary. Nothing uses the package yet; the hub
  switches to it in the next change.

## [1.0.0-beta.1] - 2026-09-14

First pre-release of the fork, installable through HACS as a custom repository.
This is the last state of the **legacy protocol** (two-byte frames, inferred
relay state) validated on hardware: live command test and an observation night
on a four-module bus on 2026-09-09. Modules reprogrammed with the "master"
firmware (state reports, XOR checksum) are handled by the 2.0 line; the legacy
path is frozen and receives fixes only.

### Added

- Config flow rewritten: the serial device is opened once before the entry is
  created (`cannot_connect` error otherwise), the device is the unique id of
  the entry (a second entry for the same adapter is refused), the module count
  is bounded to 1-7 and the "all off" scenario is entered as its 1-based number
  (stored 0-based). Reconfiguration changes the device, the module count or the
  scenario in place; entities keep their identifiers. The inert `serial_wait`
  field is gone.
- Config entry lifecycle rewritten for Home Assistant 2026: typed
  `entry.runtime_data`, awaited platform setup and unload, reader loop as an
  entry background task started once the entities exist, `ConfigEntryNotReady`
  when the port cannot be opened, the bus closed only when every platform
  unloaded, entry migration from version 1 (drops the inert `serial_wait`, sets
  the device as unique id, renames the upstream entity unique ids so entity ids
  survive the upgrade, removes the upstream per-relay devices).
- Devices: one "BioMatX bus" hub device and one device per module ("BioMatX
  module N", 1-based), linked with `via_device_id`.
- Lights: `assumed_state`, last state restored after a restart (before the bus
  is followed), `unavailable` while the serial link is down, translated error
  when a command cannot be sent, concurrent commands for one relay press it
  once. Unique ids are prefixed by the config entry id; names are "Relay N".
- Translations `en.json` and `fr.json` with identical keys (config flow,
  entity names, exceptions); `strings.json` removed as required for custom
  integrations.
- `hub.py` review fixes: the inferred relay state flips as soon as the press
  frame is written (the modules act on the press), commands are idempotent
  under the send lock (`async_set_relay`), the all-off scenario applies its
  effect whether observed or sent, `reset` keeps the model consistent when a
  press fails midway, the reader survives listener or decoding errors and
  reopens the link, closing waits for the reader to stop, the backoff only
  resets once data flows, stray bytes and rejected frames are counted apart
  and logged once per burst, frames from unknown emitters are rejected, and
  `socket://host:port` gateways use a plain asyncio TCP connection.
- `hub.py`: the integration owns the serial link (open, decode, send, reconnect
  with backoff, inferred relay and button states, listeners). The `biomatx`
  package is now used only as a data model. Frames for unknown modules or
  buttons (bus collisions) are counted and logged at debug level, never as
  errors. Observing the configured all-off scenario on the bus marks every
  relay off.
- Test fake for the serial link (`tests/fake_serial.py`) and frames captured on
  a real bus (`tests/frames.py`); `tests/test_hub.py` covers the hub fully.
- `hacs.json` so the repository can be installed as a HACS custom repository.
- Development tooling: `pyproject.toml` (ruff, pytest), `requirements_dev.txt`,
  `scripts/setup`, `scripts/lint`, `scripts/test`, pre-commit hooks.
- Continuous integration: ruff, pytest, hassfest and HACS validation.
- Repository metadata tests (`tests/test_repo_metadata.py`).
- `NOTICE` file with the Apache-2.0 attribution to the original author.

### Changed

- The integration no longer sends anything on the bus when it starts (upstream
  fired the all-off scenario at every connection).
- `manifest.json`: `issue_tracker` and `documentation` point to this repository,
  `integration_type` is `hub`, `loggers` declared, empty discovery keys removed,
  version restarted at `1.0.0-beta.1`.

### Removed

- `bus.py` and its process-wide `time.sleep` monkeypatch.
- The upstream `binary_sensor.py` and `services.yaml`: the button platform and
  the `reset` / `all_off` services return in the next changes, rewritten; the
  upstream `reload` service is dropped for good (Home Assistant reloads the
  config entry, and the link reconnects by itself).

[Unreleased]: https://github.com/Pilok/hass-biomatx/compare/v1.0.0-beta.1...HEAD
[1.0.0-beta.1]: https://github.com/Pilok/hass-biomatx/compare/f2ea009...v1.0.0-beta.1
