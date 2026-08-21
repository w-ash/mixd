/**
 * Type-pull for the play-history audit fixtures
 * (`web/e2e/fixtures/plays-history.ts`).
 *
 * tsconfig only includes `src/`, so importing the fixtures here is what puts
 * them in the type program — generated-schema drift becomes a `tsc` error
 * instead of a silently wrong screenshot harness.
 */
import { describe, expect, it } from "vitest";

import {
  libraryStates,
  playsStates,
  trackDetailStates,
} from "../../e2e/fixtures/plays-history";

describe("plays-history audit fixtures", () => {
  it("builds every scenario state", () => {
    expect(playsStates.populated().plays).toBeDefined();
    expect(playsStates.empty().histogram).toBeDefined();
    expect(playsStates.loading().plays).toEqual({ kind: "pending" });
    expect(libraryStates.populated().tracks).toBeDefined();
    expect(trackDetailStates.manyPlays().trackDetail).toBeDefined();
    expect(trackDetailStates.fewPlays().trackDetail).toBeDefined();
  });
});
