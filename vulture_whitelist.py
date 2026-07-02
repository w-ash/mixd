# Vulture whitelist — false positives from framework patterns.
# Run: uv run vulture  (paths configured in pyproject.toml)
#
# ignore_decorators in pyproject.toml covers: @router.*, @app.command,
# @app.callback, @app.exception_handler, @task, @flow, @field_validator,
# @model_validator, @computed_field.
#
# ignore_names in pyproject.toml covers: model_config, revision,
# down_revision, branch_labels, depends_on, upgrade, downgrade,
# do_GET, log_message.

# --- SQLAlchemy (declarative conventions) ---
type_annotation_map  # DeclarativeBase class attribute, read by SQLAlchemy internals

# --- Pydantic / TypedDict model fields (serialization, not direct attribute access) ---
severity  # Pydantic field on WorkflowValidationErrorSchema
required_config  # Pydantic field on NodeTypeInfoSchema
optional_config  # Pydantic field on NodeTypeInfoSchema
connector_links  # @computed_field on PlaylistDetailSchema
connector_names  # @computed_field on TrackDetailSchema
task_count  # @computed_field on WorkflowSummarySchema
node_types  # @computed_field on WorkflowSummarySchema
last_synced_at  # Pydantic ConnectorMetadataSchema field
issue_count  # Pydantic field on OperationRunSummarySchema (audit-log list)
retryable  # Pydantic field on OperationRun schemas — read by the frontend retry UI
theme_mode  # UserSettingsResponse / UserSettingsPatch Pydantic fields
iat  # JWTClaims TypedDict
iss  # JWTClaims TypedDict
aud  # JWTClaims TypedDict
capabilities  # ConnectorConfig TypedDict + ConnectorMetadataSchema
status_fn  # ConnectorConfig TypedDict — registry lookup by key

# --- Rich renderable protocol ---
render  # Rich __rich_console__ / render protocol

# --- httpx Auth protocol (called by httpx during request flow) ---
async_auth_flow  # @override of httpx.Auth.async_auth_flow

# --- FastAPI app-level registrations (not covered by @router.*) ---
spa_catchall  # catch-all for SPA routing, registered via app.route
run_server  # CLI entry point for uvicorn
main  # Typer entrypoint (registered via project.scripts, not @app.command)

# --- attrs field declarations (used by framework, not direct reference) ---
total_files  # attrs field on BatchImportResult
last_modified  # attrs field
progress_operation  # attrs field
include_track_metadata  # attrs field
decision  # attrs field on WorkflowNodeSummary
metric_value  # attrs field on WorkflowNodeSummary
node_details  # NodeResult TypedDict key
source_count  # attrs field on Track
source_playlist_name  # attrs field on Track
factory_created  # attrs field
attributes  # attrs field on NodeRegistration
incognito_excluded  # attrs field on ImportResult
resolution_failures  # attrs field on ImportResult
unique_tracks_processed  # attrs field on ImportResult
tracks_resolved  # attrs field on ImportResult
spotify_enhanced_count  # attrs field on ImportResult
accepted_plays  # attrs field on ImportResult
duration_excluded  # attrs field on ImportResult
first_played_dates  # attrs field
period_plays  # attrs field
dependencies  # attrs field on connector protocol
last_event_time  # attrs field on ProgressCoordinator
lastfm_album_mbid  # attrs field in connector conversion
lastfm_artist_mbid  # attrs field in connector conversion
attribute_name  # attrs field on probabilistic matcher
batch_result  # attrs field on ImportMetadata
fallback_resolved  # attrs field on ImportResult
redirect_resolved  # attrs field on ImportResult
image_url  # attrs field on ConnectorPlaylistInfo + Pydantic ConnectorPlaylistSchema
current_assignments  # attrs field + Pydantic ConnectorPlaylistSchema

# --- Protocol/interface methods (implementations called at runtime) ---
save_node_record  # WorkflowRunRepositoryProtocol
get_latest_run_for_workflow  # WorkflowRunRepositoryProtocol
delete_versions_for_workflow  # WorkflowVersionRepositoryProtocol
get_connector_metadata  # ConnectorRepositoryProtocol
error_classifier  # BaseAPIConnector property — Protocol contract
enrich_track_with_lastfm_metadata  # LastFMOperations — called by connector
find_tracks_by_mbids  # TrackRepositoryProtocol — tested, part of public API
create_review  # MatchReviewRepositoryProtocol — called by match_and_identify use case

# --- Parked decisions (v0.8.17 closeout, 2026-07-02) — see fable-sweep/README.md Deferred ---
NO_ISRC  # MatchFailureReason member: no producers since spoke 04; removal is a domain-vocabulary decision
added_at_dates  # Track metadata key: reader (sort_by_date) has no production writer; wire-or-delete decision pending

# --- Test-only methods (public API exercised by tests, not yet consumed in prod) ---
get_playlist_with_all_tracks  # SpotifyOperations — tested, called via connector
