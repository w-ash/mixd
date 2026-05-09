import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ResponsiveTable } from "./ResponsiveTable";

describe("ResponsiveTable", () => {
  it("renders both slots (CSS handles visibility)", () => {
    render(
      <ResponsiveTable
        table={<div data-testid="table-slot">table</div>}
        cards={<div data-testid="cards-slot">cards</div>}
      />,
    );
    expect(screen.getByTestId("table-slot")).toBeInTheDocument();
    expect(screen.getByTestId("cards-slot")).toBeInTheDocument();
  });

  it("forwards className to the container", () => {
    const { container } = render(
      <ResponsiveTable
        table={<span>t</span>}
        cards={<span>c</span>}
        className="custom-class"
      />,
    );
    expect(container.firstChild).toHaveClass("custom-class");
    expect(container.firstChild).toHaveClass("@container/table");
  });
});
