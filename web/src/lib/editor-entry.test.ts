import { describe, expect, it } from "vitest";

import {
  EDITOR_SEED_STATE,
  isEditorSeedState,
  resolveEditorEntry,
} from "./editor-entry";

describe("resolveEditorEntry", () => {
  it("is `load` whenever a workflow id is present, regardless of state", () => {
    expect(resolveEditorEntry("wf-1", null)).toBe("load");
    expect(resolveEditorEntry("wf-1", EDITOR_SEED_STATE)).toBe("load");
  });

  it("is `seed` for a fresh route carrying the typed seed marker", () => {
    expect(resolveEditorEntry(null, EDITOR_SEED_STATE)).toBe("seed");
  });

  it("is `blank` for a fresh route with no/foreign state", () => {
    expect(resolveEditorEntry(null, null)).toBe("blank");
    expect(resolveEditorEntry(null, undefined)).toBe("blank");
    expect(resolveEditorEntry(null, { something: "else" })).toBe("blank");
    // A stale boolean from the old implicit contract must not read as a seed.
    expect(resolveEditorEntry(null, { imported: true })).toBe("blank");
  });
});

describe("isEditorSeedState", () => {
  it("accepts only the typed seed shape", () => {
    expect(isEditorSeedState(EDITOR_SEED_STATE)).toBe(true);
    expect(isEditorSeedState({ editorEntry: "seed" })).toBe(true);
  });

  it("rejects everything else", () => {
    expect(isEditorSeedState(null)).toBe(false);
    expect(isEditorSeedState(undefined)).toBe(false);
    expect(isEditorSeedState("seed")).toBe(false);
    expect(isEditorSeedState({ editorEntry: "blank" })).toBe(false);
    expect(isEditorSeedState({ imported: true })).toBe(false);
  });
});
