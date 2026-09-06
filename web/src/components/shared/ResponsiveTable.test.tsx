import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ResponsiveTable } from "./ResponsiveTable";

/** Pushes a new content width to the observer the component registered. */
let resizeTo: ((width: number) => void) | null = null;

beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(callback: ResizeObserverCallback) {
        resizeTo = (width) => {
          callback(
            [{ contentRect: { width } } as ResizeObserverEntry],
            this as unknown as ResizeObserver,
          );
        };
      }
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
});

afterEach(() => {
  resizeTo = null;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** jsdom reports every box as 0×0; the wrapper needs a measurable width. */
function stubMeasuredWidth(width: number) {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    width,
  } as DOMRect);
}

function renderTable(className?: string) {
  return render(
    <ResponsiveTable
      className={className}
      table={<div data-testid="table-slot">table</div>}
      cards={<div data-testid="cards-slot">cards</div>}
    />,
  );
}

describe("ResponsiveTable", () => {
  it("renders only the cards branch below the 672px threshold", () => {
    stubMeasuredWidth(400);
    renderTable();

    expect(screen.getByTestId("cards-slot")).toBeInTheDocument();
    expect(screen.queryByTestId("table-slot")).not.toBeInTheDocument();
  });

  it("renders only the table branch at or above the threshold", () => {
    stubMeasuredWidth(672);
    renderTable();

    expect(screen.getByTestId("table-slot")).toBeInTheDocument();
    expect(screen.queryByTestId("cards-slot")).not.toBeInTheDocument();
  });

  it("swaps branches when the container is resized", () => {
    stubMeasuredWidth(400);
    renderTable();
    expect(screen.getByTestId("cards-slot")).toBeInTheDocument();

    act(() => resizeTo?.(900));

    expect(screen.getByTestId("table-slot")).toBeInTheDocument();
    expect(screen.queryByTestId("cards-slot")).not.toBeInTheDocument();
  });

  it("forwards className to the container", () => {
    stubMeasuredWidth(0);
    const { container } = renderTable("custom-class");

    expect(container.firstChild).toHaveClass("custom-class");
    expect(container.firstChild).toHaveClass("@container/table");
  });
});
