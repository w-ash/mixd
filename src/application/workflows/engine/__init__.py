"""Workflow execution runtime — the asyncio DAG executor and its observers.

Intentionally empty (no re-exports): importing a submodule here must not eagerly
pull a sibling, so callers import ``engine.executor`` / ``engine.observers``
explicitly. Keeps run-time engine weight out of any boot path that only needs the
node library or definition validation.
"""
