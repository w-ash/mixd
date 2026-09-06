import { act, render, screen } from "@testing-library/react";
import { useRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useContainerQuery } from "./useContainerQuery";

/** ResizeObserver stub — jsdom has none, and the global one in `setup.ts` is
 *  inert. This one lets a test push a width at the hook. */
class MockResizeObserver {
  static instances: MockResizeObserver[] = [];

  readonly callback: ResizeObserverCallback;
  target: Element | null = null;
  disconnected = false;

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
    MockResizeObserver.instances.push(this);
  }

  observe(target: Element) {
    this.target = target;
  }
  unobserve() {}
  disconnect() {
    this.disconnected = true;
  }

  emit(width: number) {
    this.callback(
      [
        { target: this.target, contentRect: { width } },
      ] as unknown as ResizeObserverEntry[],
      this as unknown as ResizeObserver,
    );
  }
}

function latestObserver(): MockResizeObserver {
  const observer = MockResizeObserver.instances.at(-1);
  if (!observer) throw new Error("nothing observed");
  return observer;
}

/** Width the element reports from its first, pre-paint measurement. */
function mockInitialWidth(width: number) {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    width,
  } as DOMRect);
}

function Probe({ minWidth = 768 }: { minWidth?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const isWide = useContainerQuery(ref, minWidth);
  return <div ref={ref}>{isWide ? "wide" : "narrow"}</div>;
}

describe("useContainerQuery", () => {
  beforeEach(() => {
    MockResizeObserver.instances = [];
    vi.stubGlobal("ResizeObserver", MockResizeObserver);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("reports narrow when nothing has been measured", () => {
    render(<Probe />);
    expect(screen.getByText("narrow")).toBeInTheDocument();
  });

  it("applies the first measurement before paint", () => {
    mockInitialWidth(1024);
    render(<Probe />);
    expect(screen.getByText("wide")).toBeInTheDocument();
  });

  it("treats the threshold as inclusive", () => {
    mockInitialWidth(768);
    render(<Probe minWidth={768} />);
    expect(screen.getByText("wide")).toBeInTheDocument();
  });

  it("flips when the observed element grows past the threshold", () => {
    render(<Probe />);
    expect(screen.getByText("narrow")).toBeInTheDocument();

    act(() => latestObserver().emit(900));
    expect(screen.getByText("wide")).toBeInTheDocument();

    act(() => latestObserver().emit(400));
    expect(screen.getByText("narrow")).toBeInTheDocument();
  });

  it("observes the element the ref points at", () => {
    render(<Probe />);
    expect(latestObserver().target).toBe(screen.getByText("narrow"));
  });

  it("disconnects on unmount", () => {
    const { unmount } = render(<Probe />);
    const observer = latestObserver();

    unmount();

    expect(observer.disconnected).toBe(true);
  });
});
