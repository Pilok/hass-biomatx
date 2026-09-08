# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
  version restarted at `1.0.0-beta.0`.

### Removed

- `bus.py` and its process-wide `time.sleep` monkeypatch.
- The upstream `binary_sensor.py` and `services.yaml`: the button platform and
  the `reset` / `all_off` services return in the next changes, rewritten; the
  upstream `reload` service is dropped for good (Home Assistant reloads the
  config entry, and the link reconnects by itself).

[Unreleased]: https://github.com/pilok/hass-biomatx/compare/f2ea009...HEAD
