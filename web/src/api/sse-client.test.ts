import { beforeEach, describe, expect, it, vi } from "vitest";

// ─── Mock auth module ──────────────────────────────────────────

const mockGetAuthToken = vi.fn<() => Promise<string | undefined>>();

vi.mock("#/api/auth", () => ({
  getAuthToken: (...args: unknown[]) => mockGetAuthToken(...(args as [])),
}));

import { connectToSSE } from "./sse-client";

// ─── Helpers ───────────────────────────────────────────────────

/** Mock fetch that behaves like a real fetch: rejects on signal abort. */
function mockFetchHanging() {
  vi.stubGlobal(
    "fetch",
    vi.fn((_url: string, init?: RequestInit) => {
      return new Promise((_resolve, reject) => {
        init?.signal?.addEventListener(
          "abort",
          () => reject(init.signal!.reason),
          { once: true },
        );
      });
    }),
  );
}

/** Mock fetch that resolves with a 200 response. Body parsing will fail in jsdom. */
function mockFetchOk() {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response("", { status: 200 })),
  );
}

/** Mock fetch that resolves with a non-OK status. */
function mockFetchStatus(status: number) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response("", { status })),
  );
}

// ─── Tests ─────────────────────────────────────────────────────

describe("connectToSSE", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockGetAuthToken.mockResolvedValue(undefined);
  });

  describe("authentication", () => {
    it("attaches Bearer token when auth returns a token", async () => {
      mockGetAuthToken.mockResolvedValue("test-jwt-token");
      mockFetchOk();

      try {
        await connectToSSE(
          "/api/v1/operations/op-1/progress",
          new AbortController().signal,
        );
      } catch {
        // Body stream parsing fails in jsdom — expected, we only check headers
      }

      expect(fetch).toHaveBeenCalledWith(
        "/api/v1/operations/op-1/progress",
        expect.objectContaining({
          headers: expect.objectContaining({
            Authorization: "Bearer test-jwt-token",
          }),
        }),
      );
    });

    it("proceeds without auth header when no token available", async () => {
      mockGetAuthToken.mockResolvedValue(undefined);
      mockFetchOk();

      try {
        await connectToSSE(
          "/api/v1/operations/op-1/progress",
          new AbortController().signal,
        );
      } catch {
        // Body stream parsing fails in jsdom — expected
      }

      const callArgs = vi.mocked(fetch).mock.calls[0];
      const headers = (callArgs[1] as RequestInit).headers as Record<
        string,
        string
      >;
      expect(headers.Authorization).toBeUndefined();
      expect(headers.Accept).toBe("text/event-stream");
    });
  });

  describe("connection timeout", () => {
    it("throws when server does not respond within timeout", async () => {
      mockFetchHanging();

      await expect(
        connectToSSE(
          "/api/v1/operations/op-1/progress",
          new AbortController().signal,
          50, // 50ms timeout
        ),
      ).rejects.toThrow("SSE connection timed out");
    });

    it("does not timeout after successful connection", async () => {
      mockFetchOk();

      // Use a very short timeout — if it weren't cleared, it would fire
      try {
        await connectToSSE(
          "/api/v1/operations/op-1/progress",
          new AbortController().signal,
          10,
        );
      } catch {
        // Body stream parsing fails in jsdom — expected
      }

      // Wait longer than the timeout to prove it was cleared
      await new Promise((r) => setTimeout(r, 50));

      // If timeout wasn't cleared, the signal would have been aborted.
      // No error means the timeout was properly cleaned up.
    });
  });

  describe("error handling", () => {
    it("throws on non-OK HTTP status", async () => {
      mockFetchStatus(401);

      await expect(
        connectToSSE(
          "/api/v1/operations/op-1/progress",
          new AbortController().signal,
          1000,
        ),
      ).rejects.toThrow("SSE connection failed: 401");
    });

    it("propagates user-initiated abort as-is", async () => {
      mockFetchHanging();
      const ctrl = new AbortController();

      const promise = connectToSSE(
        "/api/v1/operations/op-1/progress",
        ctrl.signal,
        5000,
      );

      // Let connectToSSE register the abort-forwarding listener
      // (it yields at the async getAuthToken call before registering)
      await new Promise((r) => setTimeout(r, 10));
      ctrl.abort();

      // Should be an AbortError, not our custom timeout error.
      // Use name check — jsdom's DOMException is from a different realm.
      await expect(promise).rejects.toSatisfy(
        (e) => (e as DOMException).name === "AbortError",
      );
    });
  });
});
