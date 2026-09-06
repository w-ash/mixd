import { QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import type { OperationRunSummarySchema } from "#/api/generated/model";
import { createTestQueryClient } from "#/test/query-utils";
import { server } from "#/test/setup";

import { useOperationRunRow } from "./useOperationRunRow";

function row(
  overrides: Partial<OperationRunSummarySchema> = {},
): OperationRunSummarySchema {
  return {
    id: "run-1",
    operation_id: "op-1",
    operation_type: "import_lastfm_history",
    started_at: "2026-01-01T10:00:00Z",
    ended_at: "2026-01-01T10:05:00Z",
    status: "complete",
    counts: { track_plays: 98 },
    issue_count: 0,
    retryable: false,
    initiated_by: "user",
    touched: [],
    ...overrides,
  };
}

function mockRuns(rows: OperationRunSummarySchema[], onRequest?: () => void) {
  server.use(
    http.get("*/api/v1/operation-runs", () => {
      onRequest?.();
      return HttpResponse.json({ data: rows, limit: 20, next_cursor: null });
    }),
  );
}

function wrapper() {
  const queryClient = createTestQueryClient();
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}

describe("useOperationRunRow", () => {
  it("selects the row matching the operation id", async () => {
    mockRuns([row({ id: "run-9", operation_id: "op-9" }), row()]);

    const { result } = renderHook(
      () => useOperationRunRow("op-1", { enabled: true }),
      { wrapper: wrapper() },
    );

    await waitFor(() => expect(result.current.data).not.toBeUndefined());
    expect(result.current.data?.id).toBe("run-1");
  });

  it("returns null when the user has no such run yet", async () => {
    mockRuns([row({ id: "run-9", operation_id: "op-9" })]);

    const { result } = renderHook(
      () => useOperationRunRow("op-1", { enabled: true }),
      { wrapper: wrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toBeNull();
  });

  it("shares one request between callers watching different operations", async () => {
    let requests = 0;
    mockRuns([row(), row({ id: "run-2", operation_id: "op-2" })], () => {
      requests += 1;
    });
    const Wrapper = wrapper();

    const { result } = renderHook(
      () => ({
        first: useOperationRunRow("op-1", { enabled: true }),
        second: useOperationRunRow("op-2", { enabled: true }),
      }),
      { wrapper: Wrapper },
    );

    await waitFor(() => {
      expect(result.current.first.data?.id).toBe("run-1");
      expect(result.current.second.data?.id).toBe("run-2");
    });
    expect(requests).toBe(1);
  });

  it("fetches nothing while disabled", async () => {
    let requests = 0;
    mockRuns([row()], () => {
      requests += 1;
    });

    renderHook(() => useOperationRunRow("op-1", { enabled: false }), {
      wrapper: wrapper(),
    });

    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(requests).toBe(0);
  });
});
