"""Workflow orchestration system with node-based transformation pipeline."""

import importlib

# NOTE: `run_workflow` is intentionally NOT re-exported here — importing it
# eagerly would pull the executor + connector import graph into uvicorn startup.
# Callers that need it import
# `from src.application.workflows.engine.executor import run_workflow` explicitly
# (use cases already do this lazily inside their .execute()).
# Concurrency is enforced at the DB (uq_workflow_runs_active partial unique
# index); the former in-process guard module has been removed.

# Eagerly import the node catalog for its @node registration side effects, so
# get_node() resolves every node type once this package is imported. Done via
# import_module (not `from . import catalog`) so a pure side-effect import needs
# no unused-binding suppression.
importlib.import_module(f"{__name__}.nodes.catalog")
