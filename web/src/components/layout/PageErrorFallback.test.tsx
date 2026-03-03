import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { PageErrorFallback } from "./PageErrorFallback";

describe("PageErrorFallback", () => {
  it("renders error message from Error instance", () => {
    render(
      <PageErrorFallback
        error={new Error("Something broke")}
        resetErrorBoundary={vi.fn()}
      />,
    );

    expect(screen.getByText("Something broke")).toBeInTheDocument();
    expect(screen.getByText("Something went wrong")).toBeInTheDocument();
  });

  it("renders fallback message for non-Error throws", () => {
    render(
      <PageErrorFallback error="string error" resetErrorBoundary={vi.fn()} />,
    );

    expect(
      screen.getByText("An unexpected error occurred"),
    ).toBeInTheDocument();
  });

  it("calls resetErrorBoundary on button click", async () => {
    const resetFn = vi.fn();
    render(
      <PageErrorFallback
        error={new Error("test")}
        resetErrorBoundary={resetFn}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(resetFn).toHaveBeenCalledOnce();
  });

  it("has role=alert for screen reader announcement", () => {
    render(
      <PageErrorFallback
        error={new Error("test")}
        resetErrorBoundary={vi.fn()}
      />,
    );

    expect(screen.getByRole("alert")).toBeInTheDocument();
  });
});
