/**
 * Shared SSE test helpers.
 *
 * IMPORTANT: Each test file must still declare its own vi.mock call:
 *   vi.mock("@/api/sse-client", () => ({ connectToSSE: vi.fn() }));
 *
 * The hoisted mock ensures this module's import resolves to the mocked version.
 */

import { vi } from "vitest";

import type { SSEEvent } from "@/api/sse-client";
import { connectToSSE } from "@/api/sse-client";

/** Re-export so test files can use it in assertions without a separate import. */
export { connectToSSE };

/** Mock connectToSSE to resolve with a finite sequence of events. */
export function mockSSEWithEvents(events: SSEEvent[]) {
  const gen = async function* () {
    for (const e of events) yield e;
  };
  vi.mocked(connectToSSE).mockResolvedValue(gen());
}
