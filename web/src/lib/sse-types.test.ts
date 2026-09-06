import { describe, expect, it } from "vitest";

import { parseSSEEvent, SSE_EVENT } from "./sse-types";

describe("parseSSEEvent", () => {
  it("decodes a known event into its typed payload", () => {
    const parsed = parseSSEEvent({
      event: "progress",
      data: JSON.stringify({
        operation_id: "op-1",
        current: 5,
        total: 10,
        message: "Working",
        status: "in_progress",
      }),
    });

    expect(parsed).toEqual({
      event: "progress",
      data: {
        operation_id: "op-1",
        current: 5,
        total: 10,
        message: "Working",
        status: "in_progress",
      },
    });
  });

  it("covers every wire name the API emits", () => {
    for (const name of Object.values(SSE_EVENT)) {
      expect(parseSSEEvent({ event: name, data: "{}" })).toEqual({
        event: name,
        data: {},
      });
    }
  });

  it("returns null for an event name outside the vocabulary", () => {
    expect(
      parseSSEEvent({ event: "gossip", data: JSON.stringify({ a: 1 }) }),
    ).toBeNull();
  });

  it("returns null for malformed JSON", () => {
    expect(
      parseSSEEvent({ event: "progress", data: "not-json{{{" }),
    ).toBeNull();
  });

  it("returns null for a keepalive frame with no data", () => {
    expect(parseSSEEvent({ event: "", data: "" })).toBeNull();
    expect(parseSSEEvent({ event: "progress", data: "" })).toBeNull();
  });
});
