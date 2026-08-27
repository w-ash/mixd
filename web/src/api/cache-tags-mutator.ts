import type { UseMutationOptions } from "@tanstack/react-query";

import type { CacheTag } from "./cache-tags";
import { writeTags } from "./cache-tags";

/**
 * Memo keyed by the route template. This runs on every render of every generated
 * mutation hook, and the result is a pure function of a string literal, so the
 * map is bounded by route count and never re-allocates the meta fragment.
 */
type RouteMeta = { route: string; invalidates?: readonly CacheTag[] };

const metaByRoute = new Map<string, RouteMeta>();

function metaFor(url: string): RouteMeta {
  const hit = metaByRoute.get(url);
  if (hit) return hit;
  const invalidates = writeTags(url);
  const meta: RouteMeta =
    invalidates.length > 0 ? { route: url, invalidates } : { route: url };
  metaByRoute.set(url, meta);
  return meta;
}

/**
 * Orval `override.query.mutationOptions` mutator — stamps every generated
 * mutation with the cache tags its route owns, so the global
 * `MutationCache.onSuccess` invalidates with no per-callsite code.
 *
 * `url` is a STATIC route template (`/api/v1/playlists/{playlistId}`): orval
 * rewrites its own `${param}` interpolations back to `{param}` for this
 * argument, so this never touches mutation variables.
 *
 * A caller-supplied `meta.invalidates` wins outright — the escape hatch for the
 * few writes that dirty a resource their own path doesn't own, or that want to
 * narrow to one member.
 */
export function withCacheTags<T extends Pick<UseMutationOptions, "meta">>(
  options: T,
  { url }: { url: string },
): T {
  const meta = metaFor(url);
  // A caller-supplied `invalidates` wins; `route` is always stamped so the
  // parity test can check the wiring from the route rather than the hook name.
  return options.meta?.invalidates
    ? { ...options, meta: { ...options.meta, route: url } }
    : { ...options, meta: { ...options.meta, ...meta } };
}
