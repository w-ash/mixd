/**
 * Pins the data-shaping the sidebar badge and the operations watcher both
 * depend on (the adaptive-polling wiring itself lives in useAdaptivePollingList).
 */

import { QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { createElement, type ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { server } from "#/test/setup";
import { createTestQueryClient } from "#/test/test-utils";

import { useActiveOperations } from "./useActiveOperations";

function createWrapper() {
  const client = createTestQueryClient();
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

describe("useActiveOperations", () => {
  it("asks for every running operation type and returns the rows", async () => {
    const seen: URLSearchParams[] = [];
    server.use(
      http.get("*/api/v1/operation-runs", ({ request }) => {
        seen.push(new URL(request.url).searchParams);
        return HttpResponse.json({ data: [ROW], limit: 20, next_cursor: null });
      }),
    );

    const { result } = renderHook(() => useActiveOperations(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.data).toHaveLength(1));
    expect(result.current.data?.[0].operation_id).toBe("op-1");
    // Syncs and applies count toward "something is running", not just imports.
    expect(seen[0].get("status")).toBe("running");
    expect(seen[0].get("type")).toBe("all");
  });

  it("returns an empty list on a non-200 success response", async () => {
    server.use(
      http.get(
        "*/api/v1/operation-runs",
        () => new HttpResponse(null, { status: 204 }),
      ),
    );

    const { result } = renderHook(() => useActiveOperations(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.data).toEqual([]));
  });
});
