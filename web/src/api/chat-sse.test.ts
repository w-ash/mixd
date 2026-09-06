import { beforeEach, describe, expect, it, vi } from "vitest";

const mockGetAuthToken = vi.fn<() => Promise<string | undefined>>();

vi.mock("#/api/auth", () => ({
  getAuthToken: () => mockGetAuthToken(),
}));

import { type ChatSSECallbacks, sendChatMessage } from "./chat-sse";

/** A response whose body streams the given SSE text in one chunk. */
function sseResponse(body: string, status = 200): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(body));
      controller.close();
    },
  });
  return new Response(stream, {
    status,
    headers: { "Content-Type": "text/event-stream" },
  });
}

function frames(...payloads: object[]): string {
  return payloads.map((p) => `data: ${JSON.stringify(p)}\n\n`).join("");
}

function mockFetch(response: Response | Promise<Response>) {
  const fn = vi.fn().mockReturnValue(Promise.resolve(response));
  vi.stubGlobal("fetch", fn);
  return fn;
}

function makeCallbacks(): ChatSSECallbacks {
  return {
    onToken: vi.fn(),
    onToolStart: vi.fn(),
    onToolResult: vi.fn(),
    onCodeStart: vi.fn(),
    onCodeResult: vi.fn(),
    onDone: vi.fn(),
    onError: vi.fn(),
  };
}

const MESSAGES = [{ role: "user" as const, content: "hi" }];

describe("sendChatMessage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockGetAuthToken.mockResolvedValue(undefined);
  });

  it("POSTs the request body through the shared SSE transport", async () => {
    const fetchMock = mockFetch(sseResponse(frames({ type: "done" })));
    const callbacks = makeCallbacks();

    await sendChatMessage(
      MESSAGES,
      callbacks,
      new AbortController().signal,
      undefined,
      "high",
    );

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/chat");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toMatchObject({
      messages: MESSAGES,
      effort: "high",
    });
    expect(init.headers).toMatchObject({
      Accept: "text/event-stream",
      "Content-Type": "application/json",
    });
    expect(callbacks.onDone).toHaveBeenCalledOnce();
    expect(callbacks.onError).not.toHaveBeenCalled();
  });

  it("waits out a slow turn instead of reporting a network failure", async () => {
    // The turn runs its confirmed tool calls before the first byte, so a
    // handshake deadline would fail work the server already committed.
    let respond: (value: Response) => void = () => {};
    const fetchMock = mockFetch(
      new Promise<Response>((resolve) => {
        respond = resolve;
      }),
    );
    const callbacks = makeCallbacks();
    vi.useFakeTimers();

    try {
      const sent = sendChatMessage(
        MESSAGES,
        callbacks,
        new AbortController().signal,
      );
      await vi.advanceTimersByTimeAsync(10 * 60_000);
      expect(fetchMock.mock.calls[0]?.[1].signal.aborted).toBe(false);

      respond(sseResponse(frames({ type: "done" })));
      await sent;
    } finally {
      vi.useRealTimers();
    }

    expect(callbacks.onError).not.toHaveBeenCalled();
    expect(callbacks.onDone).toHaveBeenCalled();
  });

  it("dispatches each frame type to its callback", async () => {
    mockFetch(
      sseResponse(
        frames(
          { type: "token", text: "Hel" },
          { type: "token", text: "lo" },
          { type: "tool_start", name: "search", id: "t1", kind: "write" },
          {
            type: "tool_result",
            name: "search",
            id: "t1",
            summary: { hits: 2 },
            is_error: false,
          },
          { type: "code_start", id: "c1", command: "print(1)" },
          {
            type: "code_result",
            id: "c1",
            stdout: "1",
            stderr: "",
            return_code: 0,
          },
          { type: "done" },
        ),
      ),
    );
    const callbacks = makeCallbacks();

    await sendChatMessage(MESSAGES, callbacks, new AbortController().signal);

    expect(callbacks.onToken).toHaveBeenNthCalledWith(1, "Hel");
    expect(callbacks.onToken).toHaveBeenNthCalledWith(2, "lo");
    expect(callbacks.onToolStart).toHaveBeenCalledWith("search", "t1", "write");
    expect(callbacks.onToolResult).toHaveBeenCalledWith(
      "search",
      "t1",
      { hits: 2 },
      false,
    );
    expect(callbacks.onCodeStart).toHaveBeenCalledWith("c1", "print(1)");
    expect(callbacks.onCodeResult).toHaveBeenCalledWith("c1", "1", "", 0);
    expect(callbacks.onDone).toHaveBeenCalledOnce();
  });

  it("falls back to the read tool kind for an unknown kind", async () => {
    mockFetch(
      sseResponse(
        frames(
          { type: "tool_start", name: "peek", id: "t1", kind: "mystery" },
          { type: "done" },
        ),
      ),
    );
    const callbacks = makeCallbacks();

    await sendChatMessage(MESSAGES, callbacks, new AbortController().signal);

    expect(callbacks.onToolStart).toHaveBeenCalledWith("peek", "t1", "read");
  });

  it("reports a typed error frame", async () => {
    mockFetch(
      sseResponse(
        frames({ type: "error", code: "MAX_ROUNDS_EXCEEDED", message: "Too" }),
      ),
    );
    const callbacks = makeCallbacks();

    await sendChatMessage(MESSAGES, callbacks, new AbortController().signal);

    expect(callbacks.onError).toHaveBeenCalledWith(
      "MAX_ROUNDS_EXCEEDED",
      "Too",
    );
  });

  it("reads the JSON error envelope on a non-2xx response", async () => {
    mockFetch(
      new Response(
        JSON.stringify({ error: { code: "RATE_LIMITED", message: "Slow" } }),
        { status: 429, headers: { "Content-Type": "application/json" } },
      ),
    );
    const callbacks = makeCallbacks();

    await sendChatMessage(MESSAGES, callbacks, new AbortController().signal);

    expect(callbacks.onError).toHaveBeenCalledWith("RATE_LIMITED", "Slow");
  });

  it("falls back to the status when the error body is not an envelope", async () => {
    mockFetch(new Response("<html>502</html>", { status: 502 }));
    const callbacks = makeCallbacks();

    await sendChatMessage(MESSAGES, callbacks, new AbortController().signal);

    expect(callbacks.onError).toHaveBeenCalledWith(
      "REQUEST_FAILED",
      "HTTP 502",
    );
  });

  it("reports a stream that ends without a terminal frame", async () => {
    mockFetch(sseResponse(frames({ type: "token", text: "half" })));
    const callbacks = makeCallbacks();

    await sendChatMessage(MESSAGES, callbacks, new AbortController().signal);

    expect(callbacks.onToken).toHaveBeenCalledWith("half");
    expect(callbacks.onError).toHaveBeenCalledWith(
      "STREAM_ENDED",
      "Response stream ended unexpectedly. Please try again.",
    );
  });

  it("ignores the [DONE] sentinel and unparseable frames", async () => {
    mockFetch(
      sseResponse(
        `data: [DONE]\n\ndata: not-json{{{\n\ndata: 42\n\n${frames({ type: "done" })}`,
      ),
    );
    const callbacks = makeCallbacks();

    await sendChatMessage(MESSAGES, callbacks, new AbortController().signal);

    expect(callbacks.onDone).toHaveBeenCalledOnce();
    expect(callbacks.onError).not.toHaveBeenCalled();
  });

  it("stays silent when the caller aborts", async () => {
    const ctrl = new AbortController();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_url: string, init?: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            init?.signal?.addEventListener(
              "abort",
              () => reject(init.signal?.reason),
              { once: true },
            );
          }),
      ),
    );
    const callbacks = makeCallbacks();

    const promise = sendChatMessage(MESSAGES, callbacks, ctrl.signal);
    await Promise.resolve();
    ctrl.abort();
    await promise;

    expect(callbacks.onError).not.toHaveBeenCalled();
  });

  it("reports a network failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    const callbacks = makeCallbacks();

    await sendChatMessage(MESSAGES, callbacks, new AbortController().signal);

    expect(callbacks.onError).toHaveBeenCalledWith("NETWORK_ERROR", "offline");
  });
});
