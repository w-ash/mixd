/**
 * Static schema registry mapping node types to their configuration fields.
 *
 * Sourced from backend _NODE_CONFIG_SCHEMA in validation.py.
 * 8 node types have required fields; all others have optional-only config.
 */

export interface ConfigFieldSchema {
  key: string;
  label: string;
  type: "text" | "number" | "select" | "boolean";
  required: boolean;
  placeholder?: string;
  options?: Array<{ value: string; label: string }>;
  min?: number;
  max?: number;
  defaultValue?: unknown;
  description?: string;
}

const CONNECTOR_OPTIONS = [
  { value: "spotify", label: "Spotify" },
  { value: "lastfm", label: "Last.fm" },
];

const METRIC_OPTIONS = [
  { value: "play_count", label: "Play Count" },
  { value: "lastfm_play_count", label: "Last.fm Play Count" },
  { value: "spotify_popularity", label: "Spotify Popularity" },
];

const SERVICE_OPTIONS = [{ value: "spotify", label: "Spotify" }];

export const NODE_CONFIG_SCHEMAS: Record<string, ConfigFieldSchema[]> = {
  // Sources
  "source.playlist": [
    {
      key: "playlist_id",
      label: "Playlist ID",
      type: "text",
      required: true,
      placeholder: "Spotify URI or ID",
    },
    {
      key: "connector",
      label: "Connector",
      type: "select",
      required: false,
      options: CONNECTOR_OPTIONS,
      defaultValue: "spotify",
    },
  ],
  "source.liked_tracks": [
    {
      key: "service",
      label: "Service",
      type: "select",
      required: false,
      options: SERVICE_OPTIONS,
      defaultValue: "spotify",
    },
  ],
  "source.played_tracks": [
    {
      key: "limit",
      label: "Max Tracks",
      type: "number",
      required: false,
      min: 1,
      max: 10000,
      placeholder: "500",
    },
  ],

  // Enrichers
  "enricher.lastfm": [],
  "enricher.spotify": [],
  "enricher.play_history": [],
  "enricher.spotify_liked_status": [],

  // Filters
  "filter.by_metric": [
    {
      key: "metric_name",
      label: "Metric",
      type: "select",
      required: true,
      options: METRIC_OPTIONS,
    },
    {
      key: "min_value",
      label: "Min Value",
      type: "number",
      required: false,
      placeholder: "0",
    },
    {
      key: "max_value",
      label: "Max Value",
      type: "number",
      required: false,
      placeholder: "No limit",
    },
    {
      key: "include_missing",
      label: "Include Missing",
      type: "boolean",
      required: false,
      defaultValue: false,
      description: "Include tracks with no metric data",
    },
  ],
  "filter.by_tracks": [
    {
      key: "exclusion_source",
      label: "Exclusion Source",
      type: "text",
      required: true,
      placeholder: "Task ID to exclude tracks from",
    },
  ],
  "filter.by_artists": [
    {
      key: "exclusion_source",
      label: "Exclusion Source",
      type: "text",
      required: true,
      placeholder: "Task ID to exclude artists from",
    },
  ],
  "filter.by_liked_status": [
    {
      key: "service",
      label: "Service",
      type: "select",
      required: true,
      options: SERVICE_OPTIONS,
    },
    {
      key: "liked",
      label: "Keep Liked",
      type: "boolean",
      required: false,
      defaultValue: true,
      description: "True = keep liked, False = keep unliked",
    },
  ],
  "filter.deduplicate": [],

  // Sorters
  "sorter.by_metric": [
    {
      key: "metric_name",
      label: "Metric",
      type: "select",
      required: false,
      options: METRIC_OPTIONS,
    },
    {
      key: "descending",
      label: "Descending",
      type: "boolean",
      required: false,
      defaultValue: true,
    },
  ],
  "sorter.shuffle": [
    {
      key: "seed",
      label: "Seed",
      type: "number",
      required: false,
      placeholder: "Random",
    },
  ],
  "sorter.reverse": [],

  // Selectors
  "selector.first": [
    {
      key: "count",
      label: "Count",
      type: "number",
      required: false,
      min: 1,
      placeholder: "10",
    },
  ],
  "selector.last": [
    {
      key: "count",
      label: "Count",
      type: "number",
      required: false,
      min: 1,
      placeholder: "10",
    },
  ],
  "selector.percentage": [
    {
      key: "percentage",
      label: "Percentage",
      type: "number",
      required: true,
      min: 1,
      max: 100,
      placeholder: "50",
    },
  ],
  "selector.sample": [
    {
      key: "count",
      label: "Sample Size",
      type: "number",
      required: false,
      min: 1,
      placeholder: "10",
    },
  ],

  // Combiners
  "combiner.merge": [],
  "combiner.interleave": [],
  "combiner.intersection": [],
  "combiner.difference": [],

  // Destinations
  "destination.create_playlist": [
    {
      key: "name",
      label: "Playlist Name",
      type: "text",
      required: true,
      placeholder: "My Workflow Playlist",
    },
    {
      key: "description",
      label: "Description",
      type: "text",
      required: false,
      placeholder: "Optional description",
    },
    {
      key: "connector",
      label: "Connector",
      type: "select",
      required: false,
      options: CONNECTOR_OPTIONS,
      defaultValue: "spotify",
    },
  ],
  "destination.update_playlist": [
    {
      key: "playlist_id",
      label: "Playlist ID",
      type: "text",
      required: true,
      placeholder: "Spotify URI or ID",
    },
    {
      key: "connector",
      label: "Connector",
      type: "select",
      required: false,
      options: CONNECTOR_OPTIONS,
      defaultValue: "spotify",
    },
  ],
};

/** Get config schema for a node type, falling back to empty array. */
export function getNodeConfigSchema(nodeType: string): ConfigFieldSchema[] {
  return NODE_CONFIG_SCHEMAS[nodeType] ?? [];
}
