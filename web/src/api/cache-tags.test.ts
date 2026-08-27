/**
 * Behaviour of the rule table: what a path resolves to, and what a write dirties.
 *
 * Coverage — that every real endpoint resolves at all — is `cache-tags.parity.
 * test.ts`, which drives off the generated client rather than a second list here.
 */

import { describe, expect, it } from "vitest";

import { tagsForPath, writeTags } from "./cache-tags";

describe("read dependencies", () => {
  it("gives a collection its family and no member tag", () => {
    expect(tagsForPath("/api/v1/playlists")).toEqual(["playlists", "tracks"]);
  });

  it("scopes an entity path to that entity", () => {
    expect(tagsForPath("/api/v1/playlists/abc123")).toContain(
      "playlists:abc123",
    );
  });

  it("scopes a sub-resource to its owning entity, not to itself", () => {
    const tags = tagsForPath("/api/v1/playlists/abc123/tracks");
    expect(tags).toContain("playlists:abc123");
    expect(tags).not.toContain("tracks:abc123");
  });

  it("declares cross-resource dependencies on the read side", () => {
    // Tagging a track changes the tag list, so the tag list says it depends on
    // tracks — no write-side fanout map needed.
    expect(tagsForPath("/api/v1/tags")).toContain("tracks");
    expect(tagsForPath("/api/v1/tracks")).toContain("tags");
  });

  it("keeps the workflow node catalog off the workflow write path", () => {
    expect(tagsForPath("/api/v1/workflows/nodes")).toEqual([
      "workflow-catalog",
    ]);
    expect(writeTags("/api/v1/workflows/{workflow_id}")).not.toContain(
      "workflow-catalog",
    );
  });

  it("keeps the live connector-playlist proxy off the play-import path", () => {
    expect(tagsForPath("/api/v1/connectors/spotify/playlists")).toContain(
      "connector-playlists",
    );
    expect(tagsForPath("/api/v1/connectors")).toEqual(["connectors"]);
  });
});

describe("write tags", () => {
  it("dirties only the family the route owns", () => {
    expect(writeTags("/api/v1/tags/{tag}")).toEqual(["tags"]);
    expect(writeTags("/api/v1/tracks/{track_id}/tags")).toEqual(["tracks"]);
  });

  it("is empty for a route with no cache involvement", () => {
    expect(writeTags("/api/v1/workflows/validate")).toEqual([]);
  });
});
