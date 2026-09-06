import { describe, expect, it } from "vitest";

import {
  connectStrategyFor,
  humanizeAuthError,
  isConnectable,
  tokenConnectCopyFor,
} from "./connectors";

describe("connectStrategyFor", () => {
  it.each([
    ["oauth", "oauth"],
    ["device_code", "oauth"],
    ["token", "token"],
    ["browser_bridge", "browser_bridge"],
    ["none", "none"],
    ["coming_soon", "none"],
  ] as const)("maps %s to the %s connect flow", (method, kind) => {
    expect(connectStrategyFor(method).kind).toBe(kind);
  });

  it("treats an auth method this build does not know as unconnectable", () => {
    expect(connectStrategyFor("passkey" as never).kind).toBe("none");
    expect(isConnectable("passkey" as never)).toBe(false);
  });
});

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

  it("falls back to the raw reason code for unknown codes", () => {
    expect(humanizeAuthError("some_unknown_code")).toBe("some_unknown_code");
  });
});

describe("tokenConnectCopyFor", () => {
  it("returns the provider's own token steps for a token connector", () => {
    const copy = tokenConnectCopyFor("discogs");

    expect(copy?.rationale).toMatch(/personal access token/i);
    // Step 1 links out to the page that issues the token.
    expect(copy?.steps[0]?.link?.url).toMatch(/discogs\.com/);
    // Step 2 steers past the OAuth application fields on that same page.
    expect(copy?.steps[1]?.tail).toMatch(/ignore the OAuth application fields/);
  });

  it("returns undefined for a connector this build ships no steps for", () => {
    expect(tokenConnectCopyFor("spotify")).toBeUndefined();
  });
});
