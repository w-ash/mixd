/**
 * One vocabulary for cache invalidation: resource tags derived from URL paths.
 *
 * Orval query keys always lead with the request path, and the `mutationOptions`
 * mutator hands every generated mutation its route template — so a tag is
 * *derived*, never registered. Adding an endpoint under an existing path family
 * needs no edit here; adding one under a new path fails the parity test until a
 * rule exists, which is the guardrail that keeps this table honest.
 *
 * Tag names come from the backend (`OperationRunSummarySchemaTouchedItem`), so
 * the tags a server operation reports and the tags a client write dirties are
 * one vocabulary checked at compile time rather than two that can drift.
 */

import type { Query, QueryClient } from "@tanstack/react-query";

import { OperationRunSummarySchemaTouchedItem } from "#/api/generated/model";

/** A resource family (`playlists`), as named by the backend. */
export type CacheFamily = OperationRunSummarySchemaTouchedItem;

/** A family, or one member of it (`playlists:abc123`). */
export type CacheTag = CacheFamily | `${CacheFamily}:${string}`;

/**
 * One path family. `owns` is what a write here dirties; `owns` plus `alsoReads`
 * is what a read here depends on. Declaring dependencies on the read side is what
 * removes the need for a separate write-fanout map.
 *
 * `scoped` opts the family into member tags, and is explicit because a path
 * cannot tell an id from a named sub-collection on its own — `/workflows/{id}`
 * names an entity, `/workflows/nodes` does not.
 */
type Rule = {
  readonly match: RegExp;
  /** Absent for paths outside the cache model — see the `RULES` header. */
  readonly owns?: CacheFamily;
  readonly alsoReads?: readonly CacheFamily[];
  readonly scoped?: true;
};

/**
 * Path rules, **first match wins** — specific rows precede general ones.
 *
 * A row with no `owns` is outside the cache model: read-shaped POSTs (preview,
 * validate, chat, key-test) and long-operation starters, whose real invalidation
 * arrives later on the terminal frame's `touched`.
 */
const RULES: readonly Rule[] = [
  // ── no cache involvement ────────────────────────────────────────────────
  { match: /^\/api\/v1\/chat/ },
  { match: /^\/api\/v1\/health/ },
  { match: /^\/api\/v1\/oauth\// },
  { match: /^\/api\/v1\/operations\// },
  { match: /^\/\.well-known\// },
  { match: /^\/webhooks\// },
  { match: /^\/auth\// },
  { match: /^\/api\/v1\/assistant\/key\/test/ },
  // Long-operation starters: the 202 says nothing changed yet. Listed after the
  // queue rule below would be wrong — this pattern would shadow it.
  {
    match: /^\/api\/v1\/imports\/spotify\/history\/queue/,
    owns: "import-queue",
  },
  { match: /^\/api\/v1\/imports\/(lastfm|spotify|apple)\// },
  { match: /^\/api\/v1\/connectors\/[^/]+\/playlists\/import/ },
  { match: /^\/api\/v1\/playlist-assignments\/apply-bulk/ },
  { match: /^\/api\/v1\/playlists\/[^/]+\/links\/[^/]+\/sync/ },
  { match: /^\/api\/v1\/operation-runs\/[^/]+\/retry-failed/ },
  { match: /^\/api\/v1\/workflows\/(preview|validate)/ },
  { match: /^\/api\/v1\/workflows\/[^/]+\/preview/ },
  { match: /^\/api\/v1\/connectors\/[^/]+\/auth-url/ },
  { match: /^\/api\/v1\/connectors\/apple_music\/musickit-config/ },

  // ── resources ───────────────────────────────────────────────────────────
  { match: /^\/api\/v1\/assistant\//, owns: "assistant" },
  { match: /^\/api\/v1\/imports\/checkpoints/, owns: "checkpoints" },
  // Proxies the live Spotify/Tidal API — split out of `connectors` so a play
  // import never speculatively refetches it and burns rate limit.
  {
    match: /^\/api\/v1\/connectors\/[^/]+\/playlists/,
    owns: "connector-playlists",
    alsoReads: ["playlist-assignments"],
  },
  { match: /^\/api\/v1\/connectors/, owns: "connectors", scoped: true },
  {
    match: /^\/api\/v1\/playlist-assignments/,
    owns: "playlist-assignments",
    scoped: true,
  },
  {
    match: /^\/api\/v1\/playlists/,
    owns: "playlists",
    alsoReads: ["tracks"],
    scoped: true,
  },
  { match: /^\/api\/v1\/plays/, owns: "plays" },
  { match: /^\/api\/v1\/reviews/, owns: "reviews", alsoReads: ["tracks"] },
  {
    match: /^\/api\/v1\/(schedules|sync\/schedules|sync\/targets)/,
    owns: "schedules",
  },
  { match: /^\/api\/v1\/settings/, owns: "settings" },
  { match: /^\/api\/v1\/stats/, owns: "stats", alsoReads: ["tracks", "plays"] },
  { match: /^\/api\/v1\/tags/, owns: "tags", alsoReads: ["tracks"] },
  {
    match: /^\/api\/v1\/tracks/,
    owns: "tracks",
    alsoReads: ["tags"],
    scoped: true,
  },
  // The node catalog and template gallery are the largest static payloads in the
  // app and sit on the editor's hottest write path. No mutation dirties them.
  { match: /^\/api\/v1\/workflows\/templates\/[^/]+\/use/, owns: "workflows" },
  {
    match: /^\/api\/v1\/workflows\/(nodes|templates)/,
    owns: "workflow-catalog",
  },
  { match: /^\/api\/v1\/workflows\/active-runs/, owns: "workflow-runs" },
  { match: /^\/api\/v1\/workflows\/[^/]+\/runs?(\/|$)/, owns: "workflow-runs" },
  { match: /^\/api\/v1\/workflows\/[^/]+\/schedule/, owns: "schedules" },
  {
    match: /^\/api\/v1\/workflows/,
    owns: "workflows",
    alsoReads: ["workflow-runs"],
    scoped: true,
  },
  { match: /^\/api\/v1\/operation-runs/, owns: "operation-runs", scoped: true },
];

/** Path segment that scopes a tag to one member, if the path names one. */
const MEMBER = /^\/api\/v1\/[^/]+\/([^/?]+)/;

/**
 * Memo keyed by the path with entity ids masked, because a cache keyed by the
 * raw path would hold one entry per entity URL ever seen and never shrink. No
 * rule discriminates on an id's value, so the masked path is the real key.
 */
const ruleCache = new Map<string, Rule | undefined>();

/** `/api/v1/playlists/abc/tracks` → `/api/v1/playlists/*​/tracks`. */
function maskIds(path: string): string {
  return path.replace(/\/(?:[0-9a-f-]{8,}|\{[^}]+\}|\d+)(?=\/|$)/gi, "/*");
}

function ruleForPath(path: string): Rule | undefined {
  const key = maskIds(path);
  if (ruleCache.has(key)) return ruleCache.get(key);
  const rule = RULES.find(({ match }) => match.test(path));
  ruleCache.set(key, rule);
  return rule;
}

const NO_TAGS: readonly CacheTag[] = [];
const NO_FAMILIES: readonly CacheFamily[] = [];

/**
 * Families memoized on the same masked path as `ruleForPath` — see there.
 *
 * Families ONLY. A member tag names one entity, so caching it under a key that
 * masked that entity's id away would hand every sibling the first one's tag.
 */
const familyCache = new Map<string, readonly CacheFamily[]>();

function familiesForPath(path: string): readonly CacheFamily[] {
  const key = maskIds(path);
  const hit = familyCache.get(key);
  if (hit) return hit;
  const rule = ruleForPath(path);
  const families: readonly CacheFamily[] = rule?.owns
    ? [rule.owns, ...(rule.alsoReads ?? [])]
    : NO_FAMILIES;
  familyCache.set(key, families);
  return families;
}

/**
 * Tags a read at `path` depends on — its families plus, when the path names one
 * entity, that entity's member tag. `[]` for paths outside the cache model.
 */
export function tagsForPath(path: string): readonly CacheTag[] {
  const families = familiesForPath(path);
  const rule = ruleForPath(path);
  if (!rule?.owns || !rule.scoped) return families;
  const member = MEMBER.exec(path)?.[1];
  // Only the owning family gets a member tag: a track's detail is scoped to
  // that track, but the tag list it also depends on is not. Built per call —
  // it is the one part of the answer the masked memo key cannot hold.
  return member
    ? [...families, `${rule.owns}:${member}` as CacheTag]
    : families;
}

/**
 * Tags a write to `url` dirties — the owning family only.
 *
 * Deliberately not member-scoped: the mutator sees `{playlistId}` as a
 * placeholder, never a value, so auto-scoping would mean mapping param names
 * onto mutation variables in the global handler. A callsite that wants the
 * narrower behaviour already has the id as a prop and passes
 * `meta: { invalidates: [`tracks:${trackId}`] }`.
 */
export function writeTags(url: string): readonly CacheTag[] {
  const owns = ruleForPath(url)?.owns;
  return owns ? [owns] : NO_TAGS;
}

const KNOWN_FAMILIES: ReadonlySet<string> = new Set(
  Object.values(OperationRunSummarySchemaTouchedItem),
);

/** Bounds how long a write waits on a matched query's initial fetch. */
const SETTLE_TIMEOUT_MS = 3_000;

function isUnknown(tag: string): boolean {
  return !KNOWN_FAMILIES.has(tag.split(":")[0] ?? tag);
}

/**
 * Invalidate every cached query that depends on any of `tags`.
 *
 * Owns the first-fetch race in one place. query-core 5.101 *erases* an
 * invalidation issued during a matched query's initial fetch: `Query.fetch`
 * joins the in-flight retryer promise instead of restarting (only
 * `data !== undefined && cancelRefetch` restarts), and the success reducer then
 * resets `isInvalidated` to false. No focus or mount refetch recovers it — which
 * is why a connect that landed mid-fetch stayed stale until a reload. So let
 * those settle first, then invalidate for real.
 *
 * Resolves once the refetches it triggered have landed, so a caller that needs
 * "the screen is true now" can await it.
 */
export async function invalidateTags(
  queryClient: QueryClient,
  tags: readonly string[],
): Promise<void> {
  if (tags.length === 0) return;
  if (import.meta.env.DEV) {
    for (const tag of tags) {
      if (isUnknown(tag)) {
        // A backend deploy may name a tag this bundle predates. Staleness there
        // is the pre-existing behaviour, not a crash.
        console.warn(`[cache-tags] unknown tag "${tag}" — ignored`);
      }
    }
  }
  const wanted = new Set(tags);
  const predicate = (query: Query) => {
    const head = query.queryKey[0];
    if (typeof head !== "string") return false;
    return tagsForPath(head).some((tag) => wanted.has(tag));
  };

  const inFlight = queryClient
    .getQueryCache()
    .findAll({ predicate, fetchStatus: "fetching" })
    .flatMap((query) =>
      query.state.data === undefined && query.promise ? [query.promise] : [],
    );
  if (inFlight.length > 0) {
    // Clear the loser: an uncleared timer keeps a jsdom test environment's queue
    // live past the test, and fires stale under fake timers.
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      await Promise.race([
        Promise.allSettled(inFlight),
        new Promise((resolve) => {
          timer = setTimeout(resolve, SETTLE_TIMEOUT_MS);
        }),
      ]);
    } finally {
      clearTimeout(timer);
    }
  }

  await queryClient.invalidateQueries({ predicate, refetchType: "active" });
}
