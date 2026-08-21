import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PlaysBarChart } from "./PlaysBarChart";

const BINS = [
  { bucket_start: "2026-08-01T00:00:00Z", count: 4 },
  { bucket_start: "2026-08-02T00:00:00Z", count: 9 },
];

describe("PlaysBarChart", () => {
  it("fires onBarClick with the clicked bin's start", () => {
    const onBarClick = vi.fn();
    // Fixed width: jsdom gives ResponsiveContainer zero size.
    const { container } = render(
      <PlaysBarChart
        bins={BINS}
        bucket="day"
        onBarClick={onBarClick}
        width={400}
        height={160}
      />,
    );

    const bars = container.querySelectorAll(".recharts-bar-rectangle");
    expect(bars.length).toBe(2);
    fireEvent.click(bars[1]);
    expect(onBarClick).toHaveBeenCalledWith("2026-08-02T00:00:00Z");
  });

  it("renders nothing for an empty histogram", () => {
    const { container } = render(
      <PlaysBarChart bins={[]} bucket="day" width={400} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
