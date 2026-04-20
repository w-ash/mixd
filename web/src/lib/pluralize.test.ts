import { describe, expect, it } from "vitest";

import { pluralize, pluralSuffix } from "./pluralize";

describe("pluralize", () => {
  it("uses the singular form when count is 1", () => {
    expect(pluralize(1, "track")).toBe("1 track");
  });

  it("uses the default plural form for any other count", () => {
    expect(pluralize(0, "track")).toBe("0 tracks");
    expect(pluralize(5, "track")).toBe("5 tracks");
  });

  it("honours an explicit irregular plural", () => {
    expect(pluralize(2, "entry", "entries")).toBe("2 entries");
  });
});

describe("pluralSuffix", () => {
  it.each([
    [1, ""],
    [0, "s"],
    [5, "s"],
  ])("returns %s for count=%i", (count, expected) => {
    expect(pluralSuffix(count)).toBe(expected);
  });
});
