/**
 * Behaviour of the invalidation mechanism itself.
 *
 * These are the regressions the milestone exists for: the erased-invalidation
 * race that left a connected card reading disconnected, and the ordering
 * contract that decides whether a dialog closes optimistically or waits.
 */

import { type QueryClient, QueryObserver } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createTestQueryClient, wasInvalidated } from "#/test/query-utils";
import { invalidateTags, tagsForPath } from "./cache-tags";

const CONNECTORS = ["/api/v1/connectors"] as const;
const TRACKS = ["/api/v1/tracks"] as const;

describe("tag matching", () => {
  let client: QueryClient;

  beforeEach(() => {
    client = createTestQueryClient();
  });

  it("invalidates a query whose path depends on the tag", async () => {
    client.setQueryData(CONNECTORS, { data: [] });

    await invalidateTags(client, ["connectors"]);

    expect(wasInvalidated(client, CONNECTORS)).toBe(true);
  });

  it("leaves an unrelated query alone", async () => {
    client.setQueryData(CONNECTORS, { data: [] });
    client.setQueryData(TRACKS, { data: [] });

    await invalidateTags(client, ["connectors"]);

    expect(wasInvalidated(client, TRACKS)).toBe(false);
  });

  it("follows a read-side dependency across resources", async () => {
    // The tag list depends on tracks, so tagging a track refreshes it without
    // the write needing to name `tags`.
    client.setQueryData(["/api/v1/tags"], { data: [] });

    await invalidateTags(client, ["tracks"]);

    expect(wasInvalidated(client, ["/api/v1/tags"])).toBe(true);
  });

  // Real ids, not "t1"/"t2": the rule memo is keyed on the path with ids
  // MASKED, and only an id long enough to be masked exercises that key — short
  // stand-ins pass whether or not the memo confuses two entities.
  const TRACK_A = "019d0000-0000-7000-8000-00000000000a";
  const TRACK_B = "019d0000-0000-7000-8000-00000000000b";

  it("narrows to one member when given a member tag", async () => {
    client.setQueryData([`/api/v1/tracks/${TRACK_A}`], { data: {} });
    client.setQueryData([`/api/v1/tracks/${TRACK_B}`], { data: {} });

    await invalidateTags(client, [`tracks:${TRACK_A}`]);

    expect(wasInvalidated(client, [`/api/v1/tracks/${TRACK_A}`])).toBe(true);
    expect(wasInvalidated(client, [`/api/v1/tracks/${TRACK_B}`])).toBe(false);
  });

  it("gives each entity its own member tag, whatever the memo saw first", () => {
    // Both paths mask to `/api/v1/tracks/*`, so a memo holding the whole tag
    // list would hand the second one the first one's member tag.
    expect(tagsForPath(`/api/v1/tracks/${TRACK_A}`)).toContain(
      `tracks:${TRACK_A}`,
    );
    expect(tagsForPath(`/api/v1/tracks/${TRACK_B}`)).toContain(
      `tracks:${TRACK_B}`,
    );
    expect(tagsForPath(`/api/v1/tracks/${TRACK_B}`)).not.toContain(
      `tracks:${TRACK_A}`,
    );
  });

  it("ignores an unknown tag with a dev warning rather than throwing", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    client.setQueryData(CONNECTORS, { data: [] });

    // A backend deploy may name a tag this bundle predates.
    await expect(
      invalidateTags(client, ["a-tag-from-the-future"]),
    ).resolves.toBeUndefined();

    expect(wasInvalidated(client, CONNECTORS)).toBe(false);
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  it("does nothing when the tag list is empty", async () => {
    client.setQueryData(CONNECTORS, { data: [] });

    await invalidateTags(client, []);

    expect(wasInvalidated(client, CONNECTORS)).toBe(false);
  });

  it("refetches /sync/targets on a connect but not on a schedule write", async () => {
    const SYNC_TARGETS = ["/api/v1/sync/targets"];
    client.setQueryData(SYNC_TARGETS, { data: [] });

    await invalidateTags(client, ["schedules"]);
    expect(wasInvalidated(client, SYNC_TARGETS)).toBe(false);

    await invalidateTags(client, ["connectors"]);
    expect(wasInvalidated(client, SYNC_TARGETS)).toBe(true);
  });
});

describe("the first-fetch race", () => {
  it("restarts a query invalidated during its initial fetch", async () => {
    // query-core joins an in-flight *initial* fetch rather than restarting it,
    // and its success reducer then clears `isInvalidated` — so without the
    // settle-first guard the invalidation is erased outright and the screen
    // keeps pre-write data until a manual reload. This is that bug: the page
    // mounts off an OAuth redirect, so the connectors GET is still open when
    // the connect completes.
    const client = createTestQueryClient();
    let calls = 0;
    let release: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });

    // A mounted observer is what makes the query "active" — an unobserved query
    // is marked invalidated but never refetched, which is not the case at issue.
    const observer = new QueryObserver(client, {
      queryKey: CONNECTORS,
      queryFn: async () => {
        calls += 1;
        if (calls === 1) await gate;
        return { call: calls };
      },
    });
    const unsubscribe = observer.subscribe(() => undefined);
    try {
      await vi.waitFor(() => expect(calls).toBe(1));

      // The write lands while that first fetch is still open.
      const invalidating = invalidateTags(client, ["connectors"]);
      release?.();
      await invalidating;

      expect(calls).toBe(2);
      expect(client.getQueryData<{ call: number }>(CONNECTORS)?.call).toBe(2);
    } finally {
      unsubscribe();
    }
  });
});

describe("mutation ordering", () => {
  it("does not hold mutateAsync open by default", async () => {
    // The default protects every optimistic dialog close in the app: the global
    // handler is awaited BEFORE the mutation's own onSuccess, so awaiting
    // unconditionally would put a refetch round trip in front of all of them.
    const client = createTestQueryClient();
    client.setQueryData(CONNECTORS, { data: [] });
    const order: string[] = [];

    await client
      .getMutationCache()
      .build(client, {
        mutationFn: async () => "done",
        meta: { invalidates: ["connectors"] },
        onSuccess: () => {
          order.push("onSuccess");
        },
      })
      .execute(undefined);

    // The write's own onSuccess still ran, and the invalidation was requested
    // rather than waited on — nothing put a round trip in front of the close.
    expect(order).toEqual(["onSuccess"]);
    expect(wasInvalidated(client, CONNECTORS)).toBe(true);
  });

  it("holds mutateAsync open when the caller asks it to", async () => {
    const client = createTestQueryClient();
    const order: string[] = [];
    client.setQueryData(CONNECTORS, { data: [] });

    await client
      .getMutationCache()
      .build(client, {
        mutationFn: async () => {
          order.push("write");
          return "done";
        },
        meta: { invalidates: ["connectors"], awaitInvalidation: true },
        onSuccess: () => {
          order.push("onSuccess");
        },
      })
      .execute(undefined);

    // The global handler runs — and is awaited — before the local one, which is
    // what lets a connect flow declare success only once the card is true.
    expect(order).toEqual(["write", "onSuccess"]);
    expect(wasInvalidated(client, CONNECTORS)).toBe(true);
  });
});
