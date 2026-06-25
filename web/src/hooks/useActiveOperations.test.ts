/**
 * Pins the data-shaping the sidebar badge and the operations watcher both
 * depend on (the adaptive-polling wiring itself lives in useAdaptivePollingList).
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("#/api/client", () => ({
  customFetch: vi.fn(),
}));

import { customFetch } from "#/api/client";

import { useActiveOperations } from "./useActiveOperations";

function createWrapper() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(QueryClientProvider, { client }, children);
  };
}

const ROW = {
  id: "run-1",
  operation_id: "op-1",
  operation_type: "import_connector_playlists",
  started_at: "2026-06-23T00:00:00Z",
  ended_at: null,
  status: "running",
  counts: {},
  issue_count: 0,
  retryable: false,
};

function mockOk(rows: object[]) {
  vi.mocked(customFetch).mockResolvedValue({
    data: { data: rows, limit: 20, next_cursor: null },
    status: 200,
    headers: new Headers(),
  });
}

describe("useActiveOperations", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("returns the in-flight rows", async () => {
    mockOk([ROW]);
    const { result } = renderHook(() => useActiveOperations(), {
      wrapper: createWrapper(),
    });
    await waitFor(() => expect(result.current.data).toHaveLength(1));
    expect(result.current.data?.[0].operation_id).toBe("op-1");
  });

  it("returns an empty list on a non-200 response", async () => {
    vi.mocked(customFetch).mockResolvedValue({
      data: null,
      status: 500,
      headers: new Headers(),
    });
    const { result } = renderHook(() => useActiveOperations(), {
      wrapper: createWrapper(),
    });
    await waitFor(() => expect(result.current.data).toEqual([]));
  });
});
