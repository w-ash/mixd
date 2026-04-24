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

# --- SQLAlchemy (event callbacks, declarative conventions, ORM columns) ---
connection_record  # required param in event.listen("connect", callback) — only dbapi_connection is used
type_annotation_map  # DeclarativeBase class attribute, read by SQLAlchemy internals
last_sync_started_at  # DB model column on DBPlaylistLink

# --- Pydantic / TypedDict model fields (serialization, not direct attribute access) ---
severity  # Pydantic field on WorkflowValidationErrorSchema
required_config  # Pydantic field on NodeTypeInfoSchema
optional_config  # Pydantic field on NodeTypeInfoSchema
connector_links  # @computed_field on PlaylistDetailSchema
connector_names  # @computed_field on TrackDetailSchema
task_count  # @computed_field on WorkflowSummarySchema
node_types  # @computed_field on WorkflowSummarySchema
token_type  # Pydantic field on SpotifyTokenResponse
scope  # Pydantic field on SpotifyTokenResponse
ean  # Pydantic field on SpotifyExternalIds
upc  # Pydantic field on SpotifyExternalIds
previous  # Pydantic field on SpotifyPaginatedResponse
SPOTIFY_TOKEN_URL  # URL constant, triggers S105 false positive
database  # MixdSettings nested model field
last_synced_at  # Pydantic ConnectorMetadataSchema field
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
ErrorResponse  # Referenced in FastAPI response_model declarations

# --- attrs field declarations (used by framework, not direct reference) ---
total_files  # attrs field on BatchImportResult
last_modified  # attrs field
operations_requested  # attrs field
is_auth_error  # attrs field
progress_operation  # attrs field
include_track_metadata  # attrs field
preserve_timestamps  # attrs field
max_api_calls  # attrs field
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
country_code  # attrs field on ISRCValidationResult
registrant_code  # attrs field on ISRCValidationResult
year  # attrs field on ISRCValidationResult
designation_code  # attrs field on ISRCValidationResult
attribute_name  # attrs field on probabilistic matcher
batch_result  # attrs field on ImportMetadata
fallback_resolved  # attrs field on ImportResult
redirect_resolved  # attrs field on ImportResult
image_url  # attrs field on ConnectorPlaylistInfo + Pydantic ConnectorPlaylistSchema
current_assignments  # attrs field + Pydantic ConnectorPlaylistSchema

# --- MatchingConfig / MatchingSettings fields (attrs + Pydantic, accessed via config object) ---
base_confidence_isrc  # MatchingConfig field
base_confidence_mbid  # MatchingConfig field
base_confidence_artist_title  # MatchingConfig field
isrc_suspect_base_confidence  # MatchingConfig field
threshold_isrc  # MatchingConfig field
threshold_mbid  # MatchingConfig field
threshold_artist_title  # MatchingConfig field
threshold_default  # MatchingConfig field
duration_missing_penalty  # MatchingConfig field
duration_max_penalty  # MatchingConfig field
duration_tolerance_ms  # MatchingConfig field
duration_per_second_penalty  # MatchingConfig field
title_max_penalty  # MatchingConfig field
artist_max_penalty  # MatchingConfig field

# --- Enum members (completeness of the enum, used externally or for display) ---
STARTED  # ProgressStatus enum
RATE_LIMITED  # MatchOutcome enum
AUTH_ERROR  # MatchOutcome enum
TRACK_NAME  # SpotifyExportField enum
ARTIST_NAME  # SpotifyExportField enum
ALBUM_NAME  # SpotifyExportField enum
SPOTIFY_TRACK_URI  # SpotifyExportField enum
PLATFORM  # SpotifyExportField enum
COUNTRY  # SpotifyExportField enum
REASON_START  # SpotifyExportField enum
REASON_END  # SpotifyExportField enum
SHUFFLE  # SpotifyExportField enum
OFFLINE  # SpotifyExportField enum
INCOGNITO_MODE  # SpotifyExportField enum
PULL  # SyncDirection enum

# --- Protocol/interface methods (implementations called at runtime) ---
save_node_record  # WorkflowRunRepositoryProtocol
get_latest_run_for_workflow  # WorkflowRunRepositoryProtocol
delete_versions_for_workflow  # WorkflowVersionRepositoryProtocol
get_connector_metadata  # ConnectorRepositoryProtocol
error_classifier  # BaseAPIConnector property — Protocol contract
enrich_track_with_lastfm_metadata  # LastFMOperations — called by connector
find_tracks_by_mbids  # TrackRepositoryProtocol — tested, part of public API
validate_isrc_structure  # Domain matching function — tested, public API
create_review  # MatchReviewRepositoryProtocol — called by match_and_identify use case

# --- Test-only methods (public API exercised by tests, not yet consumed in prod) ---
is_display_active  # RichProgressProvider property
active_operation_count  # RichProgressProvider property
get_plays_by_batch  # PlaysRepository — tested in integration
with_custom  # MetadataBuilder — tested
build_dict  # MetadataBuilder — tested
with_connector_playlist_id  # Playlist — tested
is_running  # OperationProgress — tested
get_operation  # ProgressCoordinator — tested
get_active_operations  # ProgressCoordinator — tested
cleanup_completed_operations  # ProgressCoordinator — tested
get_by_connector  # PlaylistMapper — protocol requirement
get_playlist_items  # SpotifyAPIClient — used by diagnostic scripts
get_playlist_with_all_tracks  # SpotifyOperations — tested, called via connector
get_supported_services  # PlayImportRegistry — public API (unused but logical)
track_mapper  # ConnectorTrackRepository internal dependency

# --- Rich progress column config ---
_show_rate  # Rich progress column config

# --- Constants forming complete sets ---
MAX_PAGE_SIZE  # BusinessLimits — counterpart to DEFAULT_PAGE_SIZE
RUN_STATUS_CANCELLED  # WorkflowConstants — part of status lifecycle

# --- Kept as documented contract (spec'd but not yet surfaced in a use case) ---
# Thin date-range primitives retained on purpose; re-promote by deleting when the
# Dashboard / audit-log consumer lands and becomes the caller.
list_by_tagged_at  # contract: v0.7.2 tag spec — temporal companion to add_tags
list_by_preferred_at  # contract: v0.7.0 preference spec — temporal companion to set_preferences
