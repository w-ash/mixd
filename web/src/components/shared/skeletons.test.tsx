import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  BlocksSkeleton,
  CardGridSkeleton,
  DetailHeaderSkeleton,
  ListRowsSkeleton,
} from "./skeletons";

function shimmerCount(container: HTMLElement) {
  return container.querySelectorAll('[data-slot="skeleton"]').length;
}

describe("ListRowsSkeleton", () => {
  it("renders rows × bars shimmer elements", () => {
    const { container } = render(
      <ListRowsSkeleton rows={5} bars={["h-5 w-48", "h-5 w-16", "h-5 w-24"]} />,
    );

    expect(shimmerCount(container)).toBe(15);
  });

  it("wraps rows in bordered chrome for the card variant only", () => {
    const { container: plain } = render(
      <ListRowsSkeleton rows={2} bars={["h-4 w-32"]} />,
    );
    const { container: card } = render(
      <ListRowsSkeleton rows={2} bars={["h-4 w-32"]} variant="card" />,
    );

    expect(plain.querySelectorAll(".rounded-lg.border")).toHaveLength(0);
    expect(card.querySelectorAll(".rounded-lg.border")).toHaveLength(2);
  });
});

describe("BlocksSkeleton", () => {
  it("renders count blocks with the given classes", () => {
    const { container } = render(
      <BlocksSkeleton count={4} className="h-16 w-full rounded-lg" />,
    );

    expect(shimmerCount(container)).toBe(4);
    expect(container.querySelectorAll(".h-16.rounded-lg")).toHaveLength(4);
  });
});

describe("CardGridSkeleton", () => {
  it("renders bars inside card chrome when bars are given", () => {
    const { container } = render(
      <CardGridSkeleton
        count={4}
        gridClassName="sm:grid-cols-2 lg:grid-cols-4"
        bars={["size-5", "h-8 w-24", "h-3 w-16"]}
      />,
    );

    expect(shimmerCount(container)).toBe(12);
    expect(container.querySelectorAll(".rounded-xl.border")).toHaveLength(4);
  });

  it("renders solid cells when bars are omitted", () => {
    const { container } = render(
      <CardGridSkeleton count={4} gridClassName="grid-cols-2" />,
    );

    expect(shimmerCount(container)).toBe(4);
    expect(container.querySelectorAll(".rounded-xl.border")).toHaveLength(0);
  });
});

describe("DetailHeaderSkeleton", () => {
  it("renders the title + subtitle stanza with the default subtitle width", () => {
    const { container } = render(<DetailHeaderSkeleton />);

    expect(shimmerCount(container)).toBe(2);
    expect(container.querySelector(".w-96")).not.toBeNull();
  });

  it("accepts a subtitle width override", () => {
    const { container } = render(<DetailHeaderSkeleton subtitleWidth="w-48" />);

    expect(container.querySelector(".w-48")).not.toBeNull();
    expect(container.querySelector(".w-96")).toBeNull();
  });
});
