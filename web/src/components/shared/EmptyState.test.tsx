import { describe, expect, it } from "vitest";

import { renderWithProviders, screen } from "#/test/test-utils";

import { EmptyState } from "./EmptyState";

describe("EmptyState", () => {
  it("renders heading and description", () => {
    renderWithProviders(
      <EmptyState heading="No items" description="Nothing to show here." />,
    );

    expect(screen.getByText("No items")).toBeInTheDocument();
    expect(screen.getByText("Nothing to show here.")).toBeInTheDocument();
  });

  it("renders icon when provided", () => {
    renderWithProviders(<EmptyState icon="?" heading="Not found" />);

    expect(screen.getByText("?")).toBeInTheDocument();
    expect(screen.getByText("?")).toHaveAttribute("aria-hidden", "true");
  });

  it("omits icon when not provided", () => {
    const { container } = renderWithProviders(
      <EmptyState heading="No items" />,
    );

    expect(container.querySelector("[aria-hidden]")).not.toBeInTheDocument();
  });

  it("renders action slot when provided", () => {
    renderWithProviders(
      <EmptyState
        heading="Empty"
        action={<button type="button">Add item</button>}
      />,
    );

    expect(
      screen.getByRole("button", { name: "Add item" }),
    ).toBeInTheDocument();
  });

  it("omits description when not provided", () => {
    renderWithProviders(<EmptyState heading="Heading only" />);

    expect(screen.getByText("Heading only")).toBeInTheDocument();
    expect(screen.queryByRole("paragraph")).not.toBeInTheDocument();
  });
});
