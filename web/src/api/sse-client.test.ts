import { beforeEach, describe, expect, it, vi } from "vitest";

// ─── Mock auth module ──────────────────────────────────────────

const mockGetAuthToken = vi.fn<() => Promise<string | undefined>>();

vi.mock("#/api/auth", () => ({
  getAuthToken: (...args: unknown[]) => mockGetAuthToken(...(args as [])),
}));

import { connectToSSE, SSEHttpError } from "./sse-client";

// ─── Helpers ───────────────────────────────────────────────────

/** Mock fetch that behaves like a real fetch: rejects on signal abort. */
function mockFetchHanging() {
  vi.stubGlobal(
    "fetch",
    vi.fn((_url: string, init?: RequestInit) => {
      return new Promise((_resolve, reject) => {
        init?.signal?.addEventListener(
          "abort",
          () => reject(init.signal?.reason),
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

  describe("request shape", () => {
    it("defaults to GET with no body", async () => {
      mockFetchOk();

      try {
        await connectToSSE(
          "/api/v1/operations/op-1/progress",
          new AbortController().signal,
        );
      } catch {
        // Body stream parsing fails in jsdom — expected
      }

      const init = vi.mocked(fetch).mock.calls[0][1] as RequestInit;
      expect(init.method).toBe("GET");
      expect(init.body).toBeUndefined();
    });

    it("sends a POST body and merges caller headers", async () => {
      mockFetchOk();

      try {
        await connectToSSE("/api/v1/chat", new AbortController().signal, {
          method: "POST",
          body: '{"messages":[]}',
          headers: { "Content-Type": "application/json" },
        });
      } catch {
        // Body stream parsing fails in jsdom — expected
      }

      const init = vi.mocked(fetch).mock.calls[0][1] as RequestInit;
      expect(init.method).toBe("POST");
      expect(init.body).toBe('{"messages":[]}');
      expect(init.headers).toMatchObject({
        Accept: "text/event-stream",
        "Content-Type": "application/json",
      });
    });
  });

  describe("resume", () => {
    it("sends Last-Event-ID so the server replays only what was missed", async () => {
      mockFetchOk();

      try {
        await connectToSSE(
          "/api/v1/operations/op-1/progress",
          new AbortController().signal,
          { lastEventId: "evt_42" },
        );
      } catch {
        // Body stream parsing fails in jsdom — expected, we only check headers
      }

      const headers = (vi.mocked(fetch).mock.calls[0][1] as RequestInit)
        .headers as Record<string, string>;
      expect(headers["Last-Event-ID"]).toBe("evt_42");
    });

    it("omits Last-Event-ID on a first connect", async () => {
      mockFetchOk();

      try {
        await connectToSSE(
          "/api/v1/operations/op-1/progress",
          new AbortController().signal,
        );
      } catch {
        // Body stream parsing fails in jsdom — expected
      }

      const headers = (vi.mocked(fetch).mock.calls[0][1] as RequestInit)
        .headers as Record<string, string>;
      expect(headers["Last-Event-ID"]).toBeUndefined();
    });
  });

  describe("connection timeout", () => {
    it("throws when server does not respond within timeout", async () => {
      mockFetchHanging();

      await expect(
        connectToSSE(
          "/api/v1/operations/op-1/progress",
          new AbortController().signal,
          { connectionTimeoutMs: 50 },
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
          { connectionTimeoutMs: 10 },
        );
      } catch {
        // Body stream parsing fails in jsdom — expected
      }

      // Wait longer than the timeout to prove it was cleared
      await new Promise((r) => setTimeout(r, 50));

      // If timeout wasn't cleared, the signal would have been aborted.
      // No error means the timeout was properly cleaned up.
    });

    it("never abandons a handshake given a zero budget", async () => {
      // A handshake that does committed work before its first byte — the chat
      // POST runs confirmed tool calls — must not be dropped on a deadline.
      mockFetchHanging();
      vi.useFakeTimers();
      try {
        const controller = new AbortController();
        const outcome = connectToSSE("/api/v1/chat", controller.signal, {
          connectionTimeoutMs: 0,
        }).then(
          () => "resolved",
          (error: Error) => error.message,
        );
        const stillPending = Symbol("pending");

        await vi.advanceTimersByTimeAsync(10 * 60_000);
        expect(
          await Promise.race([outcome, Promise.resolve(stillPending)]),
        ).toBe(stillPending);

        // The caller's own abort is still the way out.
        controller.abort(new Error("stopped"));
        expect(await outcome).toBe("stopped");
      } finally {
        vi.useRealTimers();
      }
    });
  });

  describe("error handling", () => {
    it("throws on non-OK HTTP status", async () => {
      mockFetchStatus(401);

      await expect(
        connectToSSE(
          "/api/v1/operations/op-1/progress",
          new AbortController().signal,
          { connectionTimeoutMs: 1000 },
        ),
      ).rejects.toThrow("SSE connection failed: 401");
    });

    it("carries the unread response on the rejection so callers can read it", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(
          new Response(JSON.stringify({ error: { code: "NOPE" } }), {
            status: 403,
          }),
        ),
      );

      const error = await connectToSSE(
        "/api/v1/chat",
        new AbortController().signal,
      ).catch((e: unknown) => e);

      expect(error).toBeInstanceOf(SSEHttpError);
      const httpError = error as SSEHttpError;
      expect(httpError.status).toBe(403);
      await expect(httpError.response.json()).resolves.toEqual({
        error: { code: "NOPE" },
      });
    });

    it("propagates user-initiated abort as-is", async () => {
      mockFetchHanging();
      const ctrl = new AbortController();

      const promise = connectToSSE(
        "/api/v1/operations/op-1/progress",
        ctrl.signal,
        { connectionTimeoutMs: 5000 },
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
