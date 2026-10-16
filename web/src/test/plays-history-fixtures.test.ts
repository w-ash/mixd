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
  // An absent mock falls through to the harness's 404, and a blank
  // screenshot still "passes" — so each state must answer its page's reads.
  it("answers every endpoint each scenario's page reads", () => {
    expect(playsStates.populated().plays?.kind).toBe("json");
    expect(playsStates.empty().histogram?.kind).toBe("json");
    expect(playsStates.loading().plays).toEqual({ kind: "pending" });
    expect(libraryStates.populated().tracks?.kind).toBe("json");
    expect(trackDetailStates.manyPlays().trackDetail?.kind).toBe("json");
    expect(trackDetailStates.fewPlays().trackDetail?.kind).toBe("json");
  });
});
