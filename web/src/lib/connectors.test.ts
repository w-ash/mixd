import { describe, expect, it } from "vitest";

import { humanizeAuthError, isConnectable } from "./connectors";

describe("isConnectable", () => {
  it.each(["oauth", "browser_bridge", "token", "device_code"] as const)(
    "treats %s as connect-capable",
    (method) => {
      expect(isConnectable(method)).toBe(true);
    },
  );

  it.each(["none", "coming_soon"] as const)(
    "treats %s as not connect-capable",
    (method) => {
      expect(isConnectable(method)).toBe(false);
    },
  );
});

describe("humanizeAuthError", () => {
  it("maps reauth_required to a session-expired reconnect message", () => {
    expect(humanizeAuthError("reauth_required")).toBe(
      "Session expired — reconnect to continue",
    );
  });

  it("maps scope_missing to a permissions reconnect message", () => {
    expect(humanizeAuthError("scope_missing")).toBe(
      "New permissions needed — reconnect to enable listening history",
    );
  });

  it("maps authorize_failed (MusicKit bridge) to a retry message", () => {
    expect(humanizeAuthError("authorize_failed")).toBe(
      "Authorization was cancelled or failed — try again",
    );
  });

  it("falls back to the raw reason code for unknown codes", () => {
    expect(humanizeAuthError("some_unknown_code")).toBe("some_unknown_code");
  });
});
