/**
 * Coverage contract for the cache-tag mechanism.
 *
 * `cache-tags.test.ts` checks the rule table against the OpenAPI spec; this
 * checks the *wiring* — that the orval mutator actually reaches every generated
 * mutation, so a write really does carry the tags its route owns. An unmapped
 * mutation is precisely the silent staleness this milestone exists to end, so
 * it fails here rather than in production.
 *
 * Driving it off the generated modules is safe outside React: the generated
 * `use*MutationOptions` bodies are pure destructuring plus closure construction,
 * and only become real hooks if the http mutator is one — `customFetch` is a
 * plain function. If that ever changes, this test breaks loudly, which is right.
 */

import { describe, expect, it } from "vitest";

import { tagsForPath } from "./cache-tags";

/** Paths that legitimately touch no cache — auth callbacks, probes, streams. */
const UNTAGGED_PATHS = new Set([
  "/.well-known/jwks.json",
  "/api/v1/chat",
  "/api/v1/chat/feedback",
  "/api/v1/health",
  "/api/v1/oauth/consent/{request_id}",
  "/api/v1/oauth/consent/{request_id}/approve",
  "/api/v1/oauth/consent/{request_id}/deny",
  "/api/v1/operations/{operation_id}/progress",
  "/api/v1/operations/{operation_id}/snapshot",
  "/auth/lastfm/callback",
  "/auth/spotify/callback",
  "/auth/tidal/callback",
  "/webhooks/neon-auth",
  "/api/v1/assistant/key/test",
  "/api/v1/connectors/apple_music/musickit-config",
  "/api/v1/connectors/{service}/auth-url",
  "/api/v1/workflows/preview",
  "/api/v1/workflows/validate",
  "/api/v1/workflows/{workflow_id}/preview",
  "/api/v1/playlists/{playlist_id}/links/{link_id}/sync/preview",
  // Long-operation starters — the terminal frame's `touched` invalidates later.
  "/api/v1/imports/apple/recent",
  "/api/v1/imports/lastfm/history",
  "/api/v1/imports/lastfm/likes",
  "/api/v1/imports/spotify/history",
  "/api/v1/imports/spotify/likes",
  "/api/v1/imports/spotify/recent",
  "/api/v1/connectors/{service}/playlists/import",
  "/api/v1/playlist-assignments/apply-bulk",
  "/api/v1/playlists/{playlist_id}/links/{link_id}/sync",
  "/api/v1/operation-runs/{run_id}/retry-failed",
]);

/**
 * `/api/v1/x/{id}` and the `/api/v1/x/undefined` a no-arg key factory produces
 * are the same route to every rule, so both collapse before comparison.
 */
function route(path: string): string {
  return path.replace(/\/(?:\{[^}]+\}|undefined)(?=\/|$)/g, "/*");
}

const UNTAGGED_ROUTES = new Set([...UNTAGGED_PATHS].map(route));

const modules = import.meta.glob("./generated/*/*.ts", {
  eager: true,
}) as Record<string, Record<string, unknown>>;

interface Entry {
  name: string;
  value: unknown;
}

const exports_: Entry[] = Object.entries(modules)
  .filter(([path]) => !path.endsWith(".msw.ts") && !path.endsWith(".faker.ts"))
  .flatMap(([, mod]) =>
    Object.entries(mod).map(([name, value]) => ({ name, value })),
  );

const mutationFactories = exports_.filter(
  (e) => /MutationOptions$/.test(e.name) && typeof e.value === "function",
);
const queryKeyFactories = exports_.filter(
  (e) => /^get.*QueryKey$/.test(e.name) && typeof e.value === "function",
);

describe("generated mutations carry cache tags", () => {
  it("found the generated modules", () => {
    expect(mutationFactories.length).toBeGreaterThan(30);
  });

  it.each(mutationFactories.map((e) => [e.name, e.value] as const))(
    "%s",
    (name, factory) => {
      const meta = (
        factory as () => {
          meta?: { route?: string; invalidates?: string[] };
        }
      )().meta;
      // The route is what decides the tags, so it is what the contract checks —
      // a hook-name allowlist would be a second spelling of UNTAGGED_PATHS.
      expect(meta?.route, `${name} was not stamped by the mutator`).toBeTypeOf(
        "string",
      );
      const tagged = (meta?.invalidates?.length ?? 0) > 0;
      expect(tagged || UNTAGGED_ROUTES.has(route(meta?.route ?? ""))).toBe(
        true,
      );
    },
  );
});

describe("generated queries resolve to cache tags", () => {
  it("found the generated key factories", () => {
    expect(queryKeyFactories.length).toBeGreaterThan(20);
  });

  it.each(queryKeyFactories.map((e) => [e.name, e.value] as const))(
    "%s",
    (name, factory) => {
      // Calling with no args yields `/api/v1/x/undefined` on a parameterised
      // route, which is deliberately still the shape the rules must handle.
      const head = String((factory as (...a: never[]) => unknown[])()[0]);
      expect(
        tagsForPath(head).length > 0 || UNTAGGED_ROUTES.has(route(head)),
        `${name} resolves to no tag and is not a declared untagged path`,
      ).toBe(true);
    },
  );
});
