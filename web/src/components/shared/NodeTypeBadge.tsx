import { cn } from "@/lib/utils";

const CATEGORY_STYLES: Record<string, { bg: string; text: string }> = {
  source: {
    bg: "bg-[oklch(0.35_0.08_250)]",
    text: "text-[oklch(0.78_0.12_250)]",
  },
  enricher: {
    bg: "bg-[oklch(0.3_0.08_300)]",
    text: "text-[oklch(0.75_0.14_300)]",
  },
  filter: {
    bg: "bg-[oklch(0.35_0.08_55)]",
    text: "text-[oklch(0.78_0.14_55)]",
  },
  sorter: {
    bg: "bg-[oklch(0.35_0.1_85)]",
    text: "text-[oklch(0.8_0.14_85)]",
  },
  selector: {
    bg: "bg-[oklch(0.3_0.06_185)]",
    text: "text-[oklch(0.75_0.1_185)]",
  },
  combiner: {
    bg: "bg-[oklch(0.3_0.08_350)]",
    text: "text-[oklch(0.75_0.14_350)]",
  },
  destination: {
    bg: "bg-[oklch(0.3_0.08_155)]",
    text: "text-[oklch(0.75_0.14_155)]",
  },
};

const FALLBACK_STYLE = {
  bg: "bg-surface-elevated",
  text: "text-text-muted",
};

interface NodeTypeBadgeProps {
  /** Node type string like "source.liked_tracks" — category is extracted from the prefix */
  nodeType: string;
  className?: string;
}

function getCategory(nodeType: string): string {
  return nodeType.split(".")[0];
}

export function NodeTypeBadge({ nodeType, className }: NodeTypeBadgeProps) {
  const category = getCategory(nodeType);
  const style = CATEGORY_STYLES[category] ?? FALLBACK_STYLE;

  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 font-display text-[11px] font-medium leading-none",
        style.bg,
        style.text,
        className,
      )}
    >
      {category}
    </span>
  );
}

/** Get the category display name from a node type string */
export function getCategoryFromNodeType(nodeType: string): string {
  return getCategory(nodeType);
}
