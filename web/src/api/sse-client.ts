/**
 * SSE transport adapter using eventsource-parser + native fetch().
 *
 * Separated from the React hook so tests can mock this module without
 * fighting jsdom's incomplete Web Streams API. Also gives us full control
 * over headers (needed for auth in v1.0) and abort signals.
 */

import { EventSourceParserStream } from "eventsource-parser/stream";

export interface SSEEvent {
  event: string;
  data: string;
  id?: string;
}

/**
 * Connect to an SSE endpoint and return an async iterable of parsed events.
 *
 * The returned promise resolves once the HTTP connection opens successfully.
 * Callers set "isConnected" at that point. The iterable then yields events
 * until the stream closes or the signal aborts.
 */
export async function connectToSSE(
  url: string,
  signal: AbortSignal,
): Promise<AsyncIterable<SSEEvent>> {
  const response = await fetch(url, {
    signal,
    headers: { Accept: "text/event-stream" },
  });

  if (!response.ok) {
    throw new Error(`SSE connection failed: ${response.status}`);
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
