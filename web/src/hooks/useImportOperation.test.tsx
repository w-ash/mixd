import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useImportOperation } from "./useImportOperation";

const mockMessage = vi.fn();
const mockError = vi.fn();
vi.mock("#/lib/toasts", async () => {
  const actual =
    await vi.importActual<typeof import("#/lib/toasts")>("#/lib/toasts");
  return {
    ...actual,
    toasts: {
      ...actual.toasts,
      message: (...args: unknown[]) => mockMessage(...args),
      error: (...args: unknown[]) => mockError(...args),
    },
  };
});

interface Variables {
  data: { mode: string };
}

interface Callbacks {
  onSuccess: (response: { status: number; data: unknown }) => void;
  onError: (error: unknown) => void;
}

/** A mutation that resolves the way each test asks it to. */
function fakeMutation(response?: { status: number; data: unknown }) {
  return {
    isPending: false,
    mutate: (_variables: Variables, { onSuccess, onError }: Callbacks) => {
      if (response) onSuccess(response);
      else onError(new Error("network down"));
    },
  };
}

beforeEach(() => {
  mockMessage.mockReset();
  mockError.mockReset();
});

describe("useImportOperation", () => {
  it("keeps the ids from an accepted start and runs onStarted", () => {
    const { result } = renderHook(() =>
      useImportOperation<Variables>(
        fakeMutation({
          status: 200,
          data: { operation_id: "op-1", run_id: "run-1" },
        }),
        "Last.fm history import",
      ),
    );
    const onStarted = vi.fn();

    act(() => result.current.trigger({ data: { mode: "recent" } }, onStarted));

    expect(result.current.operationId).toBe("op-1");
    expect(result.current.runId).toBe("run-1");
    expect(onStarted).toHaveBeenCalledOnce();
    expect(mockMessage).not.toHaveBeenCalled();
  });

  it("treats a missing run_id as no audit row", () => {
    const { result } = renderHook(() =>
      useImportOperation<Variables>(
        fakeMutation({ status: 202, data: { operation_id: "op-2" } }),
        "Spotify likes import",
      ),
    );

    act(() => result.current.trigger({ data: { mode: "recent" } }));

    expect(result.current.operationId).toBe("op-2");
    expect(result.current.runId).toBeNull();
  });

  it("reports an unexpected status and starts nothing", () => {
    const { result } = renderHook(() =>
      useImportOperation<Variables>(
        fakeMutation({ status: 204, data: undefined }),
        "Spotify likes import",
      ),
    );
    const onStarted = vi.fn();

    act(() => result.current.trigger({ data: { mode: "recent" } }, onStarted));

    expect(result.current.operationId).toBeNull();
    expect(onStarted).not.toHaveBeenCalled();
    expect(mockMessage).toHaveBeenCalledWith(
      "Failed to start Spotify likes import",
      { description: "Unexpected response (204)" },
    );
  });

  it("reports a transport failure", () => {
    const { result } = renderHook(() =>
      useImportOperation<Variables>(fakeMutation(), "Last.fm likes export"),
    );

    act(() => result.current.trigger({ data: { mode: "recent" } }));

    expect(result.current.operationId).toBeNull();
    expect(mockError).toHaveBeenCalledWith(
      "Failed to start Last.fm likes export",
      expect.any(Error),
    );
  });
});
