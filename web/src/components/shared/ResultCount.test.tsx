import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ResultCount } from "./ResultCount";

describe("ResultCount", () => {
  it.each([
    [0, "No tracks match current filters."],
    [1, "1 track matches current filters."],
    [1234, "1,234 tracks match current filters."],
  ])("announces %i results", (total, text) => {
    render(<ResultCount total={total} noun="track" />);
    expect(screen.getByText(text)).toHaveAttribute("aria-live", "polite");
  });
});
