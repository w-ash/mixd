/**
 * Shared SSE test helpers.
 *
 * IMPORTANT: Each test file must still declare its own vi.mock call:
 *   vi.mock("@/api/sse-client", () => ({ connectToSSE: vi.fn() }));
 *
 * Then import connectToSSE directly from "@/api/sse-client" for assertions.
 * Re-exporting mocked modules through intermediary files breaks in Vitest 4+.
 */

import { vi } from "vitest";

import type { SSEEvent } from "@/api/sse-client";
import { connectToSSE } from "@/api/sse-client";

/** Mock connectToSSE to resolve with a finite sequence of events. */
export function mockSSEWithEvents(events: SSEEvent[]) {
  const gen = async function* () {
    for (const e of events) yield e;
  };
  vi.mocked(connectToSSE).mockResolvedValue(gen());
}
