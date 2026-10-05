/**
 * Sanity guard for the Playlist Detail audit fixtures
 * (`web/e2e/fixtures/playlist-detail.ts`).
 *
 * The audit harness itself makes no assertions, and `tsconfig` only includes
 * `src/`, so the fixtures get no compile-time coverage on their own. Importing
 * them here pulls them into the type program AND checks at runtime that each
 * factory yields a well-formed response for every sync_status / is_resolved
 * permutation the audit relies on.
 */
import { describe, expect, it } from "vitest";

import {
  type EndpointMocks,
  linkStates,
  type MockResponse,
  makeUnresolvedEntry,
  pageStates,
  syncDialogStates,
  trackStates,
} from "../../e2e/fixtures/playlist-detail";

/** Return the body of a JSON mock. Throw when the mock is missing or not JSON. */
function jsonBody(res: MockResponse | undefined, label = "mock"): unknown {
  if (res?.kind !== "json") {
    throw new Error(`${label}: expected a json mock, got ${res?.kind}`);
  }
  return res.body;
}

describe("playlist-detail audit fixtures", () => {
  it("page-state factories cover the four states", () => {
    expect(pageStates.loading().playlist?.kind).toBe("pending");
    expect(pageStates.error().playlist).toMatchObject({
      kind: "error",
      status: 404,
    });
    const success = pageStates.success();
    for (const key of ["playlist", "tracks", "links"] as const) {
      expect(success[key]?.kind).toBe("json");
    }
  });

  it("every link factory yields a well-formed PlaylistLinkSchema array", () => {
    for (const [name, factory] of Object.entries(linkStates)) {
      const mocks = factory() as EndpointMocks;
      const links = jsonBody(mocks.links, name) as Record<string, unknown>[];
      expect(Array.isArray(links), name).toBe(true);
      for (const link of links) {
        for (const field of [
          "id",
          "connector_name",
          "sync_direction",
          "direction_label",
          "sync_status",
        ]) {
          expect(typeof link[field], `${name}.${field}`).toBe("string");
        }
      }
    }
  });

  it("unresolved entries carry a null track id and is_resolved false", () => {
    const entry = makeUnresolvedEntry(3, "Mystery Demo");
    expect(entry.is_resolved).toBe(false);
    expect(entry.track.id).toBeNull();

    const tracks = jsonBody(trackStates.withUnresolved().tracks) as {
      data: Array<{ is_resolved?: boolean }>;
    };
    expect(tracks.data.some((e) => e.is_resolved === false)).toBe(true);
  });

  it("the destructive sync preview is safety-flagged with removal counts", () => {
    const preview = jsonBody(syncDialogStates.destructive().syncPreview) as {
      safety_flagged?: boolean;
      safety_removals?: number;
    };
    expect(preview.safety_flagged).toBe(true);
    expect(preview.safety_removals ?? 0).toBeGreaterThan(0);
  });
});
