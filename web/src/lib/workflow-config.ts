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

/** Format a dotted node type into a human-readable display name (e.g. "by_metric" → "by metric"). */
export function formatNodeTypeName(nodeType: string): string {
  return nodeType.split(".").pop()?.replace(/_/g, " ") ?? nodeType;
}

/** Per-track audit record from backend TrackDecision. */
export interface TrackDecision {
  track_id: number;
  title: string;
  artists: string;
  decision: "kept" | "removed" | "added";
  reason: string;
  metric_name?: string | null;
  metric_value?: number | null;
  threshold?: number | null;
  rank?: number | null;
}
