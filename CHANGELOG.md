# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

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

- `manifest.json`: `issue_tracker` and `documentation` point to this repository,
  `integration_type` is `hub`, `loggers` declared, empty discovery keys removed,
  version restarted at `1.0.0-beta.0`.

[Unreleased]: https://github.com/pilok/hass-biomatx/compare/f2ea009...HEAD
