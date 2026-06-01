"""Workflow-definition handling — structural validation and JSON loading.

Prefect-free and engine-free by design, so FastAPI routes and the React Flow
editor can validate/load a ``WorkflowDef`` without importing the executor.
Intentionally empty (no re-exports): import ``definition.validation`` /
``definition.loader`` explicitly.
"""
