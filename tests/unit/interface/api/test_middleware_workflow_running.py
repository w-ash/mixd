"""Unit test for the WorkflowAlreadyRunningError → HTTP 409 mapping.

The DB-backed concurrency guard raises WorkflowAlreadyRunningError (from the
domain) when a workflow already has an active run. The API middleware must map
it to a 409 with a stable error envelope so the frontend can show "already
running" rather than a generic 500. Invokes the registered handler directly —
no HTTP stack — to assert the contract.
"""

import json

from fastapi import FastAPI

from src.domain.exceptions import WorkflowAlreadyRunningError
from src.interface.api.middleware import register_exception_handlers


async def test_workflow_running_maps_to_409_envelope() -> None:
    app = FastAPI()
    register_exception_handlers(app)

    handler = app.exception_handlers[WorkflowAlreadyRunningError]
    exc = WorkflowAlreadyRunningError("11111111-2222-3333-4444-555555555555")

    response = await handler(None, exc)  # request is unused by this handler

    assert response.status_code == 409
    body = json.loads(bytes(response.body))
    assert body["error"]["code"] == "WORKFLOW_RUNNING"
    assert body["error"]["message"] == str(exc)
    # workflow_id stays a string for the JSON body.
    assert body["error"]["details"]["workflow_id"] == (
        "11111111-2222-3333-4444-555555555555"
    )
    assert isinstance(body["error"]["details"]["workflow_id"], str)
