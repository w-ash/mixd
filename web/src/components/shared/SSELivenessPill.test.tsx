/**
 * Display-logic tests for the freshness pill / stall banner.
 *
 * Mocks the SSELivenessContext and useNow hooks so we can drive the
 * component through the full state matrix without a real SSE connection.
 */

import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("#/contexts/WorkflowExecutionContext", () => ({
  useSSELivenessContext: vi.fn(),
}));

vi.mock("#/hooks/useNow", () => ({
  useNow: vi.fn(),
}));

import { useSSELivenessContext } from "#/contexts/WorkflowExecutionContext";
import { useNow } from "#/hooks/useNow";
import type { SSEState } from "#/lib/sse-types";

import { SSELivenessPill } from "./SSELivenessPill";

function setLiveness(state: SSEState, lastEventAt: number | null) {
  vi.mocked(useSSELivenessContext).mockReturnValue({
    sseState: state,
    lastEventAt,
  });
}

function setNow(ms: number) {
  vi.mocked(useNow).mockReturnValue(ms);
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("SSELivenessPill", () => {
  it("renders nothing when SSE is idle", () => {
    setLiveness({ kind: "idle" }, null);
    setNow(1000);
    const { container } = render(<SSELivenessPill />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when lastEventAt is recent (<10s)", () => {
    setLiveness({ kind: "streaming", lastEventAt: 1000 }, 1000);
    setNow(5000); // 4s elapsed, below 10s threshold
    const { container } = render(<SSELivenessPill />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders pill once lastEventAt is >=10s old", () => {
    setLiveness({ kind: "streaming", lastEventAt: 1000 }, 1000);
    setNow(12_000); // 11s elapsed
    render(<SSELivenessPill />);
    expect(screen.getByText(/Last update 11s ago/)).toBeInTheDocument();
  });

  it("uses warning styling at 30s of staleness", () => {
    setLiveness({ kind: "streaming", lastEventAt: 1000 }, 1000);
    setNow(32_000); // 31s elapsed
    render(<SSELivenessPill />);
    const node = screen.getByText(/Last update 31s ago/);
    expect(node.className).toMatch(/status-warning/);
  });

  it("uses stale copy and error styling at 60s of staleness", () => {
    setLiveness({ kind: "streaming", lastEventAt: 1000 }, 1000);
    setNow(62_000); // 61s elapsed
    render(<SSELivenessPill />);
    const node = screen.getByText(/Connection may be stale/);
    expect(node.className).toMatch(/status-error/);
  });

  it("renders the stall banner when state is stalled", () => {
    setLiveness({ kind: "stalled", lastEventAt: 1000, since: 47_000 }, 1000);
    setNow(50_000); // 49s elapsed since lastEventAt
    render(<SSELivenessPill />);
    const banner = screen.getByRole("alert");
    expect(banner).toHaveTextContent(/No update for 49s ago. Checking/);
    // aria-live ensures the screen reader picks it up
    expect(banner).toHaveAttribute("aria-live", "polite");
  });

  it("renders nothing during open-no-events (run accepted, waiting for first event)", () => {
    setLiveness({ kind: "open-no-events", openedAt: 1000 }, null);
    setNow(15_000);
    const { container } = render(<SSELivenessPill />);
    // PipelineStrip's "Initializing…" handles this state instead.
    expect(container).toBeEmptyDOMElement();
  });
});
