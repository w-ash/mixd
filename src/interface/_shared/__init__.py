"""Shared interface-layer helpers reused across the CLI and API.

These are presentation-adjacent utilities (e.g. run-lifecycle status/heartbeat
updaters) that legitimately import infrastructure for session/repo wiring and
are consumed by more than one interface. Keeping them here — rather than copied
per interface — removes the drift that let run statuses diverge between the CLI
and web paths.
"""
