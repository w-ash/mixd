import { ChevronDown, X } from "lucide-react";
import type { ConnectorMetadataSchema } from "#/api/generated/model/connectorMetadataSchema";
import type { TrackFacetsSchema } from "#/api/generated/model/trackFacetsSchema";
import type { PreferenceState } from "#/components/shared/PreferenceToggle";
import { PreferenceToggle } from "#/components/shared/PreferenceToggle";
import { TagFilter } from "#/components/shared/TagFilter";
import { Button } from "#/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "#/components/ui/select";
import { Sheet } from "#/components/ui/sheet";
import { useIsMobile } from "#/hooks/useIsMobile";
import type { TagMatchMode } from "#/lib/filters-to-workflow";
import { cn } from "#/lib/utils";

interface LibraryFilterPanelProps {
  expanded: boolean;
  /**
   * Called when the panel dismisses itself — only fires from the mobile
   * Sheet (backdrop click / ESC). The desktop disclosure stays in the DOM
   * either way and ignores this callback.
   */
  onClose?: () => void;
  // Current filter values
  preference: PreferenceState | null;
  liked: "true" | "false" | null;
  connector: string | null;
  tags: string[];
  tagMode: TagMatchMode;
  // Connector options for the Source select
  connectors: ConnectorMetadataSchema[];
  /** Optional per-facet counts (from `GET /tracks?include_facets=true`).
   * When present, each filter option renders its count inline. */
  facets?: TrackFacetsSchema | null;
  // Change handlers
  onPreferenceChange: (value: PreferenceState | null) => void;
  onLikedChange: (value: "true" | "false" | null) => void;
  onConnectorChange: (value: string | null) => void;
  onTagsChange: (tags: string[]) => void;
  onTagModeChange: (mode: TagMatchMode) => void;
}

/**
 * Grouped filter panel for the Library page.
 *
 * Renders two ways depending on the viewport:
 *
 * - **Desktop** (≥ 1024px) — an inline collapsible disclosure below the
 *   toolbar, controlled by the parent's `expanded` prop. The DOM stays
 *   mounted whether expanded or collapsed; CSS handles the slide.
 * - **Mobile** (< 1024px) — a bottom-anchored `<Sheet>` opened by the same
 *   `expanded` prop. Dismissed via `onClose` (backdrop / ESC / close button).
 *
 * All state is URL-driven: props flow in from `useSearchParams()` in the
 * parent, change handlers write back to searchParams. This component owns
 * no state of its own.
 */
export function LibraryFilterPanel({
  expanded,
  onClose,
  preference,
  liked,
  connector,
  tags,
  tagMode,
  connectors,
  facets,
  onPreferenceChange,
  onLikedChange,
  onConnectorChange,
  onTagsChange,
  onTagModeChange,
}: LibraryFilterPanelProps) {
  const isMobile = useIsMobile();

  // Helper: "(N)" suffix for a facet option, or empty string when no facet
  // data is loaded. Keeps call sites concise.
  const count = (dim: "preference" | "liked" | "connector", key: string) =>
    facets ? ` (${facets[dim][key] ?? 0})` : "";

  const sections = (
    <>
      <FilterSection label="Preference">
        <div className="flex flex-col gap-1.5">
          <PreferenceToggle
            value={preference}
            onChange={onPreferenceChange}
            size="default"
          />
          {facets && (
            <div className="flex gap-3 font-mono text-xs text-text-muted">
              {(["hmm", "nah", "yah", "star"] as const).map((state) => (
                <span
                  key={state}
                  className={cn(
                    "tabular-nums",
                    facets.preference[state] === 0 && "opacity-50",
                  )}
                >
                  {state} ({facets.preference[state] ?? 0})
                </span>
              ))}
            </div>
          )}
        </div>
      </FilterSection>

      <FilterSection label="Tags">
        <TagFilter
          tags={tags}
          mode={tagMode}
          onTagsChange={onTagsChange}
          onModeChange={onTagModeChange}
        />
      </FilterSection>

      <FilterSection label="Source">
        <div className="flex flex-wrap items-center gap-3">
          <Select
            value={liked ?? "all"}
            onValueChange={(value) =>
              onLikedChange(
                value === "all" ? null : (value as "true" | "false"),
              )
            }
          >
            <SelectTrigger aria-label="Filter by liked status" className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All tracks</SelectItem>
              <SelectItem value="true">
                Liked{count("liked", "true")}
              </SelectItem>
              <SelectItem value="false">
                Not liked{count("liked", "false")}
              </SelectItem>
            </SelectContent>
          </Select>

          <Select
            value={connector ?? "all"}
            onValueChange={(value) =>
              onConnectorChange(value === "all" ? null : value)
            }
          >
            <SelectTrigger aria-label="Filter by connector" className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All connectors</SelectItem>
              {connectors.map((c) => (
                <SelectItem key={c.name} value={c.name}>
                  {c.name.charAt(0).toUpperCase() + c.name.slice(1)}
                  {count("connector", c.name)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </FilterSection>
    </>
  );

  if (isMobile) {
    return (
      <Sheet
        open={expanded}
        onClose={() => onClose?.()}
        ariaLabel="Library filters"
      >
        <div className="flex items-center justify-between">
          <h2 className="font-display text-base font-semibold text-text">
            Filters
          </h2>
          <Button
            variant="ghost"
            size="icon"
            aria-label="Close filters"
            onClick={() => onClose?.()}
          >
            <X className="size-4" aria-hidden="true" />
          </Button>
        </div>
        <div className="mt-4 max-h-[70svh] space-y-4 overflow-y-auto pb-4">
          {sections}
        </div>
      </Sheet>
    );
  }

  return (
    <div
      id="library-filter-panel"
      data-state={expanded ? "open" : "closed"}
      aria-hidden={!expanded}
      className={cn(
        "overflow-hidden transition-[max-height,opacity,margin] duration-150 ease-out",
        expanded
          ? "mb-6 max-h-[800px] opacity-100"
          : "mb-0 max-h-0 opacity-0 pointer-events-none",
      )}
    >
      <div
        className={cn(
          "rounded-lg border border-border-muted bg-surface-sunken/50 p-5",
          "space-y-4",
        )}
      >
        {sections}
      </div>
    </div>
  );
}

/**
 * Count how many filter facets are active — drives the badge number on the
 * toolbar's "Filters" toggle. Counts tags as one facet regardless of size
 * so the number reflects "how many filter groups" not "how many values."
 */
export function countActiveFilters({
  preference,
  liked,
  connector,
  tags,
}: {
  preference: string | null;
  liked: string | null;
  connector: string | null;
  tags: string[];
}): number {
  let count = 0;
  if (preference) count += 1;
  if (liked === "true" || liked === "false") count += 1;
  if (connector) count += 1;
  if (tags.length > 0) count += 1;
  return count;
}

interface FilterSectionProps {
  label: string;
  children: React.ReactNode;
}

function FilterSection({ label, children }: FilterSectionProps) {
  return (
    <div className="flex flex-col gap-2">
      <h3 className="font-display text-xs uppercase tracking-wider text-text-muted">
        {label}
      </h3>
      {children}
    </div>
  );
}

/**
 * Chevron icon the parent toolbar button uses to reflect expand/collapse
 * state. Export so the Library page can reuse the same visual.
 */
export function FilterPanelChevron({ expanded }: { expanded: boolean }) {
  return (
    <ChevronDown
      className={cn(
        "size-3.5 transition-transform duration-150",
        expanded && "rotate-180",
      )}
      aria-hidden="true"
    />
  );
}
