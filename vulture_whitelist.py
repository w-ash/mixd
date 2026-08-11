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
run_server  # CLI entry point for uvicorn
main  # Typer entrypoint (registered via project.scripts, not @app.command)

# --- attrs field declarations (used by framework, not direct reference) ---
total_files  # attrs field on BatchImportResult
last_modified  # attrs field
progress_operation  # attrs field
include_track_metadata  # attrs field
source_count  # attrs field on Track
source_playlist_name  # attrs field on Track
factory_created  # attrs field
attributes  # attrs field on NodeRegistration
incognito_excluded  # attrs field on ImportResult
resolution_failures  # attrs field on ImportResult
unique_tracks_processed  # attrs field on ImportResult
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

# --- Parked decisions (v0.8.17 closeout, 2026-07-02) — see fable-sweep/README.md Deferred ---
NO_ISRC  # MatchFailureReason member: no producers since spoke 04; removal is a domain-vocabulary decision
added_at_dates  # Track metadata key: reader (sort_by_date) has no production writer; wire-or-delete decision pending

# --- v0.9.x agent parity: classification API consumed outside src/ (parity test + matrix generator + v0.9.3 MCP) ---
BLACKLISTED_USE_CASES  # registry parity bucket — used by test_registry_parity + generate_capability_matrix
MECHANICALLY_EXCLUDED_USE_CASES  # registry parity bucket
INTERNAL_USE_CASES  # registry parity bucket
NOT_YET_COVERED  # empty as of v0.9.1; the parity tripwire test asserts it stays empty

# --- v0.9.0 chat: voice-protocol attrs fields (rendered dynamically into the prompt) ---
voice_examples  # Voice attrs field rendered into the system prompt via the voice registry
rules  # Voice attrs field rendered into the system prompt

# --- v0.9.5 remote MCP OAuth AS: SDK-protocol methods dispatched structurally, not by name ---
# The mcp SDK's OAuthAuthorizationServerProvider Protocol + TokenVerifier Protocol are
# invoked by the SDK's own handlers (AuthorizationHandler, TokenHandler, BearerAuthBackend),
# so vulture can't see the call sites. Integration tests exercise all of them end-to-end.
register_client  # MixdOAuthProvider — DCR path, called by RegistrationHandler
authorize  # MixdOAuthProvider — called by AuthorizationHandler
exchange_authorization_code  # MixdOAuthProvider — called by TokenHandler
exchange_refresh_token  # MixdOAuthProvider — called by TokenHandler
load_access_token  # MixdOAuthProvider — Protocol member
revoke_token  # MixdOAuthProvider — Protocol member
exchange_identity_assertion  # MixdOAuthProvider — Protocol member (unsupported grant)
verify_token  # MixdTokenVerifier — called by the SDK BearerAuthBackend
token_endpoint_auth_methods_supported  # OAuthMetadata field overridden for CIMD/"none"
client_id_metadata_document_supported  # OAuthMetadata field — Anthropic clients key CIMD on it
authorization_response_iss_parameter_supported  # OAuthMetadata field — RFC 9207
ip  # _PinnedTarget field — the validated public IP the CIMD fetch connects to
jti  # MCP access-token claim (JWT id)

# --- Test-only isolation helpers (vulture excludes tests/, so it can't see the callers) ---
reset_run_activity  # src/application/services/run_activity.py — used by 3 test modules
reset_schedule_signal  # src/application/services/schedule_signal.py — used by test_schedules.py
reset_play_refresh_flight  # src/application/services/play_freshness.py — test isolation

# --- v0.10.2 identity ledger: read surfaces without an in-src caller yet ---
# Both are consumed only from outside vulture's paths (tests/) — declared substrate
# the milestone deliberately shipped without a writer. Deleting them would force a
# migration when the deferred v0.14.0 Manual Mapping UI consumer lands.
# (`unreject` left this list at v0.10.3: `mixd tracks unreject` is now a real in-src
# caller, so the script that was its only consumer — scripts/unreject_mapping_candidate.py
# — was deleted rather than kept as a second, redundant caller.)
get_supersession_chain  # mapping history walk — tests + the v0.14.0 Manual Mapping UI
events_for_mapping  # "why does my library believe this" reader — tests + v0.14.0 UI

# --- v0.10.2.13 import-queue view: response fields read only by the web client ---
# Both are set in src/ and serialized out by `ImportQueueResponse.model_validate(...,
# from_attributes=True)`; their only readers are in web/src/lib/import-queue.ts, which
# vulture cannot see. They are load-bearing there — `size_bytes` weights the "time left"
# estimate and `settled_at` is half of every per-file duration. Deleting either would
# silently blank the queue view.
size_bytes  # QueueEntry + ImportQueueEntrySchema — per-file bytes, weights the estimate
settled_at  # QueueEntry + ImportQueueEntrySchema — per-file duration, batch-rate input

# --- v0.10.3: ResolutionMetrics keys reached only through string constants ---
# All four are written by the Spotify resolver's _assemble_metrics and read in
# play_import_orchestrator via `combined_metrics[key]`, where `key` comes from
# _RUN_METRIC_KEYS / _CARRIED_RESOLUTION_METRICS. They were visible to vulture
# only by accident: _combine_phase_results used to bind one local per metric,
# and v0.10.3 replaced those hand-written blocks with a single table-driven
# loop — the DRY fix that stopped a counter reaching the run record at all.
# Whitelisting is the honest trade: the alternative is reinstating four dead
# locals purely so a tool can see a name it cannot resolve through a str key.
fallback_resolved  # ResolutionMetrics — read via _CARRIED_RESOLUTION_METRICS
redirect_resolved  # ResolutionMetrics — read via _CARRIED_RESOLUTION_METRICS
dead_ids_unresolved  # ResolutionMetrics — read via _CARRIED_RESOLUTION_METRICS
isrc_suspect_deferred  # ResolutionMetrics — read via _CARRIED_RESOLUTION_METRICS
