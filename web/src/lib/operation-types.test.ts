import { describe, expect, it } from "vitest";

import {
  isRunOperationType,
  OPERATION_TYPES,
  operationLabel,
} from "./operation-types";

describe("operationLabel", () => {
  it("returns the display label for a known operation type", () => {
    expect(operationLabel("import_lastfm_history")).toBe(
      "Last.fm history import",
    );
  });

  it("labels playlist import connector-generically", () => {
    expect(operationLabel("import_connector_playlists")).toBe(
      "Playlist import",
    );
  });

  it("falls back to a generic phrase for a type this build does not know", () => {
    expect(operationLabel("import_tidal_likes")).toBe("Operation");
  });

  it("does not treat inherited object keys as operation types", () => {
    expect(isRunOperationType("toString")).toBe(false);
    expect(operationLabel("constructor")).toBe("Operation");
  });
});

describe("OPERATION_TYPES", () => {
  it("gives every type a label and at least one count key", () => {
    for (const [type, spec] of Object.entries(OPERATION_TYPES)) {
      expect(spec.label, type).not.toBe("");
      expect(spec.countKeys.length, type).toBeGreaterThan(0);
    }
  });

  it("pluralizes titles and falls back to a zero-count phrase", () => {
    const spec = OPERATION_TYPES.import_lastfm_history;
    expect(spec.title(1)).toBe("Imported 1 scrobble");
    expect(spec.title(2)).toBe("Imported 2 scrobbles");
    expect(spec.title(0)).toBe("Import complete");
  });
});
