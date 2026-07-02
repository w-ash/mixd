/** Human-readable match method label + explanation */
const matchMethods: Record<string, { label: string; description: string }> = {
  direct_import: {
    label: "Direct",
    description: "Matched by ISRC (exact identifier)",
  },
  direct: {
    label: "Direct",
    description: "Matched by ISRC (exact identifier)",
  },
  search_fallback: {
    label: "Search",
    description: "Found via search by artist + title",
  },
  artist_title: {
    label: "Artist/Title",
    description: "Matched by artist name and track title",
  },
  spotify_redirect: {
    label: "Redirect",
    description: "Redirected from a different version",
  },
  spotify_connector_play_resolver: {
    label: "Play Resolver",
    description: "Resolved from listening history",
  },
  lastfm_discovery: {
    label: "Discovery",
    description: "Discovered via Last.fm data",
  },
  direct_import_stale_id: {
    label: "Stale ID",
    description: "Originally matched by ID, but the ID has since changed",
  },
  search_fallback_stale_id: {
    label: "Stale ID",
    description: "Originally found via search, but the ID has since changed",
  },
};

export function matchMethodLabel(method: string): string {
  return matchMethods[method]?.label ?? method;
}

export function matchMethodDescription(method: string): string {
  return matchMethods[method]?.description ?? method;
}
