# Vulture whitelist — false positives from framework patterns.
# Run: uv run vulture src/ vulture_whitelist.py

# --- Pydantic model_config (class-level config, not instance vars) ---
model_config  # noqa

# --- Pydantic computed fields / validators / model_validators ---
transform_flat_env_vars  # @model_validator(mode="before") — called by Pydantic
connector_links  # @computed_field
connector_names  # @computed_field
task_count  # @computed_field
node_types  # @computed_field
severity  # Pydantic field
required_config  # Pydantic field
optional_config  # Pydantic field
empty_mbid_to_none  # @field_validator — called by Pydantic
coerce_str_to_int  # @field_validator — called by Pydantic
coerce_single_to_list  # @field_validator — called by Pydantic

# --- FastAPI route handlers (registered via @router.get/post decorators) ---
list_playlists  # FastAPI route
backup  # Typer command
backup_playlist  # FastAPI route
show_track  # FastAPI route
track_playlists  # FastAPI route
connectors_status  # FastAPI route
get_connectors  # FastAPI route
health_check  # FastAPI route
import_lastfm_history  # FastAPI route
import_spotify_likes  # FastAPI route
export_lastfm_likes  # FastAPI route
import_spotify_history  # FastAPI route
get_checkpoints  # FastAPI route
stream_operation_progress  # FastAPI route
list_active_operations  # FastAPI route
get_playlist_tracks  # FastAPI route
get_dashboard_stats  # FastAPI route
get_track_detail  # FastAPI route
get_track_playlists  # FastAPI route
create_workflow  # FastAPI route
list_node_types  # FastAPI route
validate_workflow  # FastAPI route
preview_unsaved_workflow  # FastAPI route
preview_saved_workflow  # FastAPI route
get_workflow  # FastAPI route
update_workflow  # FastAPI route
run_workflow_endpoint  # FastAPI route
list_workflow_runs  # FastAPI route
get_workflow_run  # FastAPI route
list_workflow_versions  # FastAPI route
get_workflow_version  # FastAPI route
revert_workflow_version  # FastAPI route
list_playlist_links  # FastAPI route
create_playlist_link  # FastAPI route
delete_playlist_link  # FastAPI route
update_playlist_link  # FastAPI route
preview_playlist_sync  # FastAPI route
sync_playlist_link  # FastAPI route
list_reviews  # FastAPI route
resolve_review  # FastAPI route
get_integrity_report  # FastAPI route
get_matching_health  # FastAPI route
merge_track  # FastAPI route
relink_mapping  # FastAPI route
unlink_mapping  # FastAPI route
spa_catchall  # FastAPI catch-all for SPA routing
run_server  # CLI entry point for uvicorn

# --- FastAPI exception handlers (registered via @app.exception_handler) ---
not_found_handler  # exception handler
template_readonly_handler  # exception handler
workflow_running_handler  # exception handler
connector_not_available_handler  # exception handler
value_error_handler  # exception handler
generic_error_handler  # exception handler

# --- Typer CLI commands (registered via @app.command decorators) ---
version_command  # Typer command
init_cli  # Typer command
main  # Typer entrypoint
history_main  # Typer command
checkpoints_cmd  # Typer command
likes_main  # Typer command
workflow_main  # Typer command
export  # Typer command (workflow export)

# --- Rich renderable protocol ---
render  # Rich __rich_console__ / render protocol

# --- httpx Auth protocol (called by httpx during request flow) ---
async_auth_flow  # @override of httpx.Auth.async_auth_flow
do_GET  # HTTP handler protocol method
log_message  # HTTP handler protocol method

# --- SQLAlchemy event listeners ---
_set_sqlite_pragma  # @event.listens_for — called by SQLAlchemy engine

# --- Alembic migration variables/functions (framework-required) ---
revision  # Alembic migration
down_revision  # Alembic migration
branch_labels  # Alembic migration
depends_on  # Alembic migration
upgrade  # Alembic migration
downgrade  # Alembic migration

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

# --- Pydantic model fields (serialization, not direct access) ---
token_type  # Pydantic field on SpotifyTokenResponse
scope  # Pydantic field on SpotifyTokenResponse
ean  # Pydantic field on SpotifyExternalIds
upc  # Pydantic field on SpotifyExternalIds
previous  # Pydantic field on SpotifyPaginatedResponse
SPOTIFY_TOKEN_URL  # URL constant, triggers S105 false positive

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

# --- Pydantic error response schema (used in OpenAPI spec) ---
ErrorResponse  # Referenced in FastAPI response_model declarations

# --- Constants forming complete sets ---
MAX_PAGE_SIZE  # BusinessLimits — counterpart to DEFAULT_PAGE_SIZE
RUN_STATUS_CANCELLED  # WorkflowConstants — part of status lifecycle

# --- Settings fields (used by Pydantic model construction) ---
database  # NaradaSettings nested model field
last_sync_started_at  # DB model column on DBPlaylistLink

# --- Use case validators (used in attrs field(validator=...)) ---
tracklist_or_connector_playlist  # attrs validator
