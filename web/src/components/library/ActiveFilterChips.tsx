import { DismissibleChip } from "#/components/shared/DismissibleChip";
import { TagChip } from "#/components/shared/TagChip";
import { recencyLabel } from "#/lib/play-filters";
import { cn } from "#/lib/utils";

const PREFERENCE_LABELS: Record<string, string> = {
  star: "★ Starred",
  yah: "Yah",
  hmm: "Hmm",
  nah: "Nah",
};

interface ActiveFilterChipsProps {
  search: string | null;
  liked: string | null;
  connector: string | null;
  preference: string | null;
  tags: string[];
  minPlays: string | null;
  neverPlayed: boolean;
  playedWithin: string | null;
  notPlayedWithin: string | null;
  onClearFilter: (
    key:
      | "q"
      | "liked"
      | "connector"
      | "preference"
      | "min_plays"
      | "never_played"
      | "played_within"
      | "not_played_within",
  ) => void;
  onRemoveTag: (tag: string) => void;
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
  search,
  liked,
  connector,
  preference,
  tags,
  minPlays,
  neverPlayed,
  playedWithin,
  notPlayedWithin,
  onClearFilter,
  onRemoveTag,
  onClearAll,
  className,
}: ActiveFilterChipsProps) {
  const chips: React.ReactNode[] = [];

  if (search) {
    chips.push(
      <DismissibleChip
        key="search"
        label={`Search: "${search}"`}
        onRemove={() => onClearFilter("q")}
      />,
    );
  }

  if (preference) {
    const label = PREFERENCE_LABELS[preference] ?? preference;
    chips.push(
      <DismissibleChip
        key="preference"
        label={`Preference: ${label}`}
        onRemove={() => onClearFilter("preference")}
      />,
    );
  }

  if (liked === "true" || liked === "false") {
    chips.push(
      <DismissibleChip
        key="liked"
        label={liked === "true" ? "Liked" : "Not liked"}
        onRemove={() => onClearFilter("liked")}
      />,
    );
  }

  if (connector) {
    const label = connector.charAt(0).toUpperCase() + connector.slice(1);
    chips.push(
      <DismissibleChip
        key="connector"
        label={`Source: ${label}`}
        onRemove={() => onClearFilter("connector")}
      />,
    );
  }

  if (neverPlayed) {
    chips.push(
      <DismissibleChip
        key="never-played"
        label="Never played"
        onRemove={() => onClearFilter("never_played")}
      />,
    );
  }

  if (minPlays) {
    chips.push(
      <DismissibleChip
        key="min-plays"
        label={`${minPlays}+ plays`}
        onRemove={() => onClearFilter("min_plays")}
      />,
    );
  }

  if (playedWithin) {
    chips.push(
      <DismissibleChip
        key="played-within"
        label={recencyLabel(playedWithin, false)}
        onRemove={() => onClearFilter("played_within")}
      />,
    );
  }

  if (notPlayedWithin) {
    chips.push(
      <DismissibleChip
        key="not-played-within"
        label={recencyLabel(notPlayedWithin, true)}
        onRemove={() => onClearFilter("not_played_within")}
      />,
    );
  }

  for (const tag of tags) {
    chips.push(
      <TagChip
        key={`tag-${tag}`}
        tag={tag}
        onRemove={() => onRemoveTag(tag)}
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
