---
name: new-connector
description: Step-by-step guide for adding a new external service connector to narada — Pydantic models, API client, conversions, matching provider
disable-model-invocation: true
---

# Adding a New External Service Connector

Follow these 5 steps to add a new connector. Each step creates one file in `src/infrastructure/connectors/new_service/`.

## Step 1: Define Pydantic models for API response shapes

```python
# src/infrastructure/connectors/new_service/models.py
class NewServiceBaseModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

class NewServiceTrack(NewServiceBaseModel):
    id: str
    name: str
    # ... typed fields matching API JSON shape
```

## Step 2: Validate raw dict -> typed model at the API client boundary

```python
# src/infrastructure/connectors/new_service/client.py
class NewServiceAPIClient(BaseAPIClient):
    async def get_track(self, track_id: str) -> NewServiceTrack | None:
        data = response.json()
        return NewServiceTrack.model_validate(data)  # Validate here
```

## Step 3: Connector facade delegates to typed client + conversions

```python
# src/infrastructure/connectors/new_service/connector.py
class NewServiceConnector(BaseAPIConnector):
    @property
    def connector_name(self) -> str:
        return "new_service"

    def convert_track_to_connector(self, track_data: dict[str, Any]) -> ConnectorTrack:
        from .conversions import convert_new_service_track
        return convert_new_service_track(track_data)
```

## Step 4: Conversions receive typed models, not raw dicts

```python
# src/infrastructure/connectors/new_service/conversions.py
def convert_new_service_track(data: dict[str, Any] | NewServiceTrack) -> ConnectorTrack:
    track = NewServiceTrack.model_validate(data) if isinstance(data, dict) else data
    # All access is typed — no isinstance() guards needed
```

## Step 5: Matching provider works with typed models

```python
# src/infrastructure/connectors/new_service/matching_provider.py
class NewServiceMatchingProvider(BaseMatchingProvider):
    # Implement matching logic using typed models from client
```

## Key Principles

- **Validate at the boundary**: raw `dict[str, Any]` -> Pydantic model at the API client, never downstream
- **Typed everywhere**: conversions and matching receive typed models, not raw dicts
- **Extend BaseAPIClient**: use `_api_call("op_name", impl, *args)` for retry + context + suppress
- **Implement `aclose()`**: delegate to `_client.aclose()` for httpx pool cleanup
- **Retry via tenacity**: use policies from `_shared/retry_policies.py`, integrated with `ErrorClassifier`

## After Creating the Connector

1. Register in `src/infrastructure/connectors/discovery.py`
2. Add capability protocols in `src/application/connector_protocols.py`
3. Update database models if needed: `src/infrastructure/persistence/database/db_models.py`
4. Generate migration: `poetry run alembic revision --autogenerate -m "add new_service"`
5. Write tests: unit tests for client/conversions/matching, integration tests for repository
