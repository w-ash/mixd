/**
 * Shared workflow node configuration — icons, accent colors, labels.
 *
 * Used by both WorkflowGraph (full DAG) and PipelineStrip (compact inline view).
 */

import type { LucideIcon } from "lucide-react";
import {
  ArrowUpDown,
  Database,
  Filter,
  Merge,
  Send,
  Sparkles,
  Target,
} from "lucide-react";

import type { NodeType } from "#/api/generated/model";

export interface NodeCategoryConfig {
  Icon: LucideIcon;
  accentColor: string;
  label: string;
}

/**
 * One entry per node category. `satisfies Record<NodeType, …>` binds this to
 * the generated category union, so a category added server-side fails the
 * build here until it gets an icon, accent and label.
 */
const CATEGORY_CONFIG = {
  source: {
    Icon: Database,
    accentColor: "oklch(0.7 0.12 250)",
    label: "Source",
  },
  enricher: {
    Icon: Sparkles,
    accentColor: "oklch(0.7 0.14 300)",
    label: "Enricher",
  },
  filter: {
    Icon: Filter,
    accentColor: "oklch(0.75 0.14 55)",
    label: "Filter",
  },
  sorter: {
    Icon: ArrowUpDown,
    accentColor: "oklch(0.8 0.14 85)",
    label: "Sorter",
  },
  selector: {
    Icon: Target,
    accentColor: "oklch(0.7 0.1 185)",
    label: "Selector",
  },
  combiner: {
    Icon: Merge,
    accentColor: "oklch(0.7 0.14 350)",
    label: "Combiner",
  },
  destination: {
    Icon: Send,
    accentColor: "oklch(0.7 0.14 155)",
    label: "Destination",
  },
} satisfies Record<NodeType, NodeCategoryConfig>;

/** Category configs, indexable by any string (unknown categories yield undefined). */
export const NODE_CONFIG: Record<string, NodeCategoryConfig> = CATEGORY_CONFIG;

/**
 * Extract the category name from a dotted node type ("filter.by_metric" →
 * "filter"). Fallback for call sites with no loaded schema; prefer
 * `NodeSchemas.getCategory` where the API data is available.
 */
export function getNodeCategoryName(nodeType: string): string {
  return nodeType.split(".")[0];
}

/** Which connection handles a node exposes on the editor canvas. */
export interface NodeHandles {
  input: boolean;
  output: boolean;
}

/**
 * Handle visibility from the node type alone, for the window before the API
 * schemas load: a source takes no input, a destination produces no output,
 * everything else has both.
 */
export function fallbackNodeHandles(nodeType: string): NodeHandles {
  const category = getNodeCategoryName(nodeType);
  return { input: category !== "source", output: category !== "destination" };
}

/** Category config for a dotted node type, or null when the category is unknown. */
export function findNodeCategory(nodeType: string): NodeCategoryConfig | null {
  return NODE_CONFIG[getNodeCategoryName(nodeType)] ?? null;
}

/** Get category config for a dotted node type like "filter.by_metric". */
export function getNodeCategory(nodeType: string): NodeCategoryConfig {
  return findNodeCategory(nodeType) ?? NODE_CONFIG.source;
}

/**
 * Category config for a node, preferring the category the API declares over
 * the dotted node type. Pass `null` when no schema is loaded for the type.
 */
export function resolveNodeCategory(
  declared: NodeType | null,
  nodeType: string,
): NodeCategoryConfig {
  return (declared && NODE_CONFIG[declared]) ?? getNodeCategory(nodeType);
}

/** Color function for React Flow MiniMap nodes — maps category accent to a muted fill. */
export function miniMapNodeColor(node: { type?: string }) {
  const accent = NODE_CONFIG[node.type ?? ""]?.accentColor;
  return accent
    ? `color-mix(in oklch, ${accent} 35%, oklch(0.15 0.01 60))`
    : "oklch(0.25 0.01 60)";
}

/** Format a dotted node type into a human-readable display name (e.g. "by_metric" → "by metric"). */
export function formatNodeTypeName(nodeType: string): string {
  return nodeType.split(".").pop()?.replace(/_/g, " ") ?? nodeType;
}

/** Lightweight track summary for playlist change evidence. */
export interface PlaylistChangeTrack {
  track_id: number;
  title: string;
  artists: string;
}

/** Evidence of what changed in a playlist destination node. */
export interface PlaylistChanges {
  tracks_added: PlaylistChangeTrack[];
  tracks_removed: PlaylistChangeTrack[];
  tracks_added_total?: number;
  tracks_removed_total?: number;
  tracks_moved: number;
  playlist_id: string;
  connector?: string | null;
}
