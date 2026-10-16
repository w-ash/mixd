/**
 * Verifies the REST snapshot fallback hook:
 *   - skips polling when disabled (SSE healthy)
 *   - polls when enabled (SSE stalled)
 *   - parses the {data, status, headers} envelope
 *
 * Doesn't drive the full snapshot -> reconciliation flow; that's covered
 * by useWorkflowSSE.test.ts where the SSE state and node-status merge
 * are observable end-to-end.
 */

import { QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { createElement, type ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { createTestQueryClient } from "#/test/query-utils";
import { server } from "#/test/setup";

import {
  type OperationSnapshot,
  useOperationSnapshot,
} from "./useOperationSnapshot";

function createWrapper() {
  const client = createTestQueryClient();
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(QueryClientProvider, { client }, children);
  };
}

const sampleSnapshot: OperationSnapshot = {
  operation_id: "op-1",
  id: "run-1",
  workflow_id: "wf-1",
  status: "running",
  nodes: [
    {
      node_id: "n1",
      node_type: "source.playlist",
      status: "running",
      execution_order: 1,
    },
  ],
};

/** Serve `respond` for the snapshot route and record each requested op id. */
function mockSnapshot(respond: () => Response) {
  const requested: string[] = [];
  server.use(
    http.get("*/api/v1/operations/:operationId/snapshot", ({ params }) => {
      requested.push(String(params.operationId));
      return respond();
    }),
  );
  return requested;
}

describe("useOperationSnapshot", () => {
  it("does not call the endpoint when disabled", async () => {
    const requested = mockSnapshot(() => HttpResponse.json(sampleSnapshot));

    const { result } = renderHook(
      () => useOperationSnapshot("op-1", { enabled: false }),
      { wrapper: createWrapper() },
    );

    await new Promise((r) => setTimeout(r, 20));
    expect(requested).toEqual([]);
    expect(result.current.data).toBeUndefined();
  });

  it("fetches the operation's snapshot and unwraps the envelope when enabled", async () => {
    const requested = mockSnapshot(() => HttpResponse.json(sampleSnapshot));

    const { result } = renderHook(
      () => useOperationSnapshot("op-1", { enabled: true }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => {
      expect(result.current.data).toEqual(sampleSnapshot);
    });
    expect(requested[0]).toBe("op-1");
  });

  it("surfaces a failed fetch as an error after one attempt", async () => {
    const requested = mockSnapshot(() =>
      HttpResponse.json(
        { error: { code: "NOT_FOUND", message: "Operation not found" } },
        { status: 404 },
      ),
    );

    const { result } = renderHook(
      () => useOperationSnapshot("op-bad", { enabled: true }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => {
      expect(result.current.isError).toBe(true);
    });
    expect(result.current.error?.message).toBe("Operation not found");
    expect(requested).toEqual(["op-bad"]);
  });
});
