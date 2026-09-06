import { DismissibleChip } from "#/components/shared/DismissibleChip";
import { TagChip } from "#/components/shared/TagChip";
import type {
  LibraryFilters,
  SetLibraryFilter,
} from "#/hooks/useLibraryFilters";
import { getConnectorLabel } from "#/lib/connector-brand";
import { recencyLabel } from "#/lib/play-filters";
import { cn } from "#/lib/utils";

const PREFERENCE_LABELS: Record<string, string> = {
  star: "★ Starred",
  yah: "Yah",
  hmm: "Hmm",
  nah: "Nah",
};

interface ActiveFilterChipsProps {
  filters: LibraryFilters;
  setFilter: SetLibraryFilter;
  onClearAll: () => void;
  className?: string;
}

/**
 * Row of dismissible chips above the track table — gives users a visible,
 * targeted way to drop individual filters.
 *
 * Returns `null` when no filters are active so the surrounding layout
 * collapses naturally rather than showing an empty bar.
 */
export function ActiveFilterChips({
  filters,
  setFilter,
  onClearAll,
  className,
}: ActiveFilterChipsProps) {
  const chips: React.ReactNode[] = [];

  if (filters.search) {
    chips.push(
      <DismissibleChip
        key="search"
        label={`Search: "${filters.search}"`}
        onRemove={() => setFilter("search", null)}
      />,
    );
  }

  if (filters.preference) {
    const label = PREFERENCE_LABELS[filters.preference] ?? filters.preference;
    chips.push(
      <DismissibleChip
        key="preference"
        label={`Preference: ${label}`}
        onRemove={() => setFilter("preference", null)}
      />,
    );
  }

  if (filters.liked) {
    chips.push(
      <DismissibleChip
        key="liked"
        label={filters.liked === "true" ? "Liked" : "Not liked"}
        onRemove={() => setFilter("liked", null)}
      />,
    );
  }

  if (filters.connector) {
    chips.push(
      <DismissibleChip
        key="connector"
        label={`Source: ${getConnectorLabel(filters.connector)}`}
        onRemove={() => setFilter("connector", null)}
      />,
    );
  }

  if (filters.neverPlayed) {
    chips.push(
      <DismissibleChip
        key="never-played"
        label="Never played"
        onRemove={() => setFilter("neverPlayed", null)}
      />,
    );
  }

  if (filters.minPlays !== null) {
    chips.push(
      <DismissibleChip
        key="min-plays"
        label={`${filters.minPlays}+ plays`}
        onRemove={() => setFilter("minPlays", null)}
      />,
    );
  }

  if (filters.playedWithin !== null) {
    chips.push(
      <DismissibleChip
        key="played-within"
        label={recencyLabel(String(filters.playedWithin), false)}
        onRemove={() => setFilter("playedWithin", null)}
      />,
    );
  }

  if (filters.notPlayedWithin !== null) {
    chips.push(
      <DismissibleChip
        key="not-played-within"
        label={recencyLabel(String(filters.notPlayedWithin), true)}
        onRemove={() => setFilter("notPlayedWithin", null)}
      />,
    );
  }

  for (const tag of filters.tags) {
    chips.push(
      <TagChip
        key={`tag-${tag}`}
        tag={tag}
        onRemove={() =>
          setFilter(
            "tags",
            filters.tags.filter((t) => t !== tag),
          )
        }
      />,
    );
  }

  if (chips.length === 0) return null;

  return (
    <section
      className={cn(
        "mb-4 flex flex-wrap items-center gap-2 text-xs",
        className,
      )}
      aria-label="Active filters"
    >
      {chips}
      <button
        type="button"
        onClick={onClearAll}
        className="ml-1 rounded-sm text-text-muted underline-offset-4 transition-colors hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
      >
        Clear all
      </button>
    </section>
  );
}
