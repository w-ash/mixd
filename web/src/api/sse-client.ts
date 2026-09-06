/**
 * SSE transport adapter using eventsource-parser + native fetch().
 *
 * Separated from the React hook so tests can mock this module without
 * fighting jsdom's incomplete Web Streams API. fetch() (rather than
 * EventSource) is what gives us headers, abort signals and POST bodies —
 * the chat stream is POST, the operations stream is GET.
 */

import { EventSourceParserStream } from "eventsource-parser/stream";

import { getAuthToken } from "./auth";

export interface SSEEvent {
  event: string;
  data: string;
  id?: string;
}

export interface ConnectToSSEOptions {
  /**
   * Id of the last event the caller already processed. Sent as the
   * `Last-Event-ID` header so a resumed stream replays only what was missed —
   * the server filters on it (`routes/operations.py`). Omit on a first connect.
   */
  lastEventId?: string | null;
  /**
   * Budget for the initial HTTP handshake only, not the stream. `0` removes the
   * budget, for a handshake whose duration is genuinely unbounded.
   */
  connectionTimeoutMs?: number;
  /** Handshake method. Defaults to GET. */
  method?: string;
  /** Request body for a POST-bodied stream. Declare its type in `headers`. */
  body?: BodyInit;
  /** Extra request headers, merged over Accept + Authorization. */
  headers?: Record<string, string>;
}

/**
 * Handshake rejected with a non-2xx status.
 *
 * Carries the unread `Response` so a caller that speaks a JSON error envelope
 * can read the body before it reports the failure.
 */
export class SSEHttpError extends Error {
  readonly status: number;
  readonly response: Response;

  constructor(response: Response) {
    super(`SSE connection failed: ${response.status}`);
    this.name = "SSEHttpError";
    this.status = response.status;
    this.response = response;
  }
}

/**
 * Connect to an SSE endpoint and return an async iterable of parsed events.
 *
 * The returned promise resolves once the HTTP connection opens successfully.
 * Callers set "isConnected" at that point. The iterable then yields events
 * until the stream closes or the signal aborts.
 */
const CONNECTION_TIMEOUT_MS = 30_000;

export async function connectToSSE(
  url: string,
  signal: AbortSignal,
  options: ConnectToSSEOptions = {},
): Promise<AsyncIterable<SSEEvent>> {
  const {
    lastEventId,
    connectionTimeoutMs = CONNECTION_TIMEOUT_MS,
    method = "GET",
    body,
  } = options;
  const headers: Record<string, string> = { Accept: "text/event-stream" };

  const token = await getAuthToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  if (lastEventId) {
    headers["Last-Event-ID"] = lastEventId;
  }
  Object.assign(headers, options.headers);

  // Timeout covers only the initial HTTP connection, not the stream.
  // Uses a custom Error (not DOMException) so the hook surfaces it
  // instead of suppressing it as a user-initiated abort.
  // Both caller abort and timeout route through a single controller
  // to avoid AbortSignal.any cross-realm issues in test environments.
  const fetchCtrl = new AbortController();
  // A zero budget means no timer at all. A handshake that does committed work
  // before its first byte — the chat POST confirms tools and refreshes
  // playlists — has no honest upper bound, and aborting it would report a
  // network failure for work the server already carried out.
  const timeoutId =
    connectionTimeoutMs > 0
      ? setTimeout(
          () => fetchCtrl.abort(new Error("SSE connection timed out")),
          connectionTimeoutMs,
        )
      : undefined;
  const forwardAbort = () => fetchCtrl.abort(signal.reason);
  signal.addEventListener("abort", forwardAbort, { once: true });

  let response: Response;
  try {
    response = await fetch(url, {
      method,
      body,
      signal: fetchCtrl.signal,
      headers,
    });
  } finally {
    clearTimeout(timeoutId);
    signal.removeEventListener("abort", forwardAbort);
  }

  if (!response.ok) {
    throw new SSEHttpError(response);
  }

  if (!response.body) {
    throw new Error("SSE response has no body");
  }

  const eventStream = response.body
    .pipeThrough(new TextDecoderStream())
    .pipeThrough(new EventSourceParserStream());

  const reader = eventStream.getReader();

  // When the signal aborts, cancel the reader so reader.read() resolves
  // {done: true} — the async iterable terminates naturally without
  // Promise.race plumbing in the consumer.
  signal.addEventListener("abort", () => reader.cancel(), { once: true });

  return {
    [Symbol.asyncIterator]() {
      return {
        async next() {
          const result = await reader.read();
          if (result.done) return { done: true as const, value: undefined };
          const parsed = result.value;
          return {
            done: false as const,
            value: {
              event: parsed.event ?? "message",
              data: parsed.data,
              id: parsed.id,
            },
          };
        },
        async return() {
          await reader.cancel();
          reader.releaseLock();
          return { done: true as const, value: undefined };
        },
      };
    },
  };
}
