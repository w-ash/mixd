import { cn } from "#/lib/utils";
import { findNodeCategory, getNodeCategoryName } from "#/lib/workflow-config";

interface NodeTypeBadgeProps {
  /** Node type string like "source.liked_tracks" — the category is its prefix. */
  nodeType: string;
  className?: string;
}

/**
 * Category pill for a workflow node, tinted with the category accent from
 * `NODE_CONFIG` so a node reads the same colour and name here as on the canvas.
 * An unrecognised category falls back to muted styling and its raw prefix.
 */
export function NodeTypeBadge({ nodeType, className }: NodeTypeBadgeProps) {
  const config = findNodeCategory(nodeType);

  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 font-display text-[11px] font-medium leading-none",
        !config && "bg-surface-elevated text-text-muted",
        className,
      )}
      style={
        config
          ? {
              backgroundColor: `color-mix(in oklch, ${config.accentColor} 20%, transparent)`,
              color: config.accentColor,
            }
          : undefined
      }
    >
      {config?.label ?? getNodeCategoryName(nodeType)}
    </span>
  );
}
