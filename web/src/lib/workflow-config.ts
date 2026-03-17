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

export interface NodeCategoryConfig {
  Icon: LucideIcon;
  accentColor: string;
  label: string;
}

export const NODE_CONFIG: Record<string, NodeCategoryConfig> = {
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
};

/** Extract category name from a dotted node type like "filter.by_metric" → "filter". */
export function getNodeCategoryName(nodeType: string): string {
  return nodeType.split(".")[0];
}

/** Get category config for a dotted node type like "filter.by_metric". */
export function getNodeCategory(nodeType: string): NodeCategoryConfig {
  return NODE_CONFIG[getNodeCategoryName(nodeType)] ?? NODE_CONFIG.source;
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
