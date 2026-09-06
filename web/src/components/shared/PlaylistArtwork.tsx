import { cn } from "#/lib/utils";

interface PlaylistArtworkProps {
  /** Connector-hosted cover image; a muted placeholder stands in when absent. */
  src: string | null | undefined;
  className?: string;
}

/**
 * Square cover thumbnail for a connector playlist row.
 *
 * The image is decorative — the row's name carries the accessible label — so
 * it renders with an empty alt and the placeholder is hidden from the tree.
 */
export function PlaylistArtwork({ src, className }: PlaylistArtworkProps) {
  const shape = cn("size-10 shrink-0 rounded-sm", className);
  return src ? (
    <img
      src={src}
      alt=""
      loading="lazy"
      className={cn(shape, "object-cover")}
    />
  ) : (
    <div className={cn(shape, "bg-surface-muted")} aria-hidden="true" />
  );
}
