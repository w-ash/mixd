/**
 * Tests for the in-app Apple Music connect hook.
 *
 * Covers the MusicKit JS loader's failure hardening (retry after a
 * loaded-without-global rejection, load timeout), the mount-time prewarm
 * (config fetch + load + configure eagerly; only `authorize()` left for the
 * click), and `authorize()` rejection classification: quiet dismissal
 * (MKError ACCESS_DENIED or a cancel-shaped message) vs blocked popup
 * (actionable toast) vs real failure (error toast).
 *
 * Loader tests re-import the module via `vi.resetModules()` for a fresh
 * module-level promise cache; hook tests install a `window.MusicKit` mock,
 * which the loader's sync guard picks up (no CDN, no cache involvement).
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { toasts } from "#/lib/toasts";
import { makeConnectorMetadata } from "#/test/factories";
import { mockMusicKit } from "#/test/musickit";
import { server } from "#/test/setup";
import { createTestQueryClient } from "#/test/test-utils";

import {
  type MusicKitStatic,
  useAppleMusicConnect,
} from "./useAppleMusicConnect";

function makeWrapper() {
  const client = createTestQueryClient();
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

/** Register the happy-path connect routes, counting config fetches. */
function stubConnectRoutes() {
  const counters = { configFetches: 0 };
  server.use(
    http.get("*/api/v1/connectors/apple_music/musickit-config", () => {
      counters.configFetches += 1;
      return HttpResponse.json({ developer_token: "dev-jwt" });
    }),
    http.post(
      "*/api/v1/connectors/apple_music/token",
      () => new HttpResponse(null, { status: 204 }),
    ),
    http.get("*/api/v1/connectors", () =>
      HttpResponse.json([
        makeConnectorMetadata({ name: "apple_music", connected: true }),
      ]),
    ),
  );
  return counters;
}

afterEach(() => {
  window.MusicKit = undefined;
  // Toast spies must not accumulate calls across tests.
  vi.restoreAllMocks();
});

describe("useAppleMusicConnect prewarm", () => {
  it("prewarms config + configure on mount; the click only runs authorize()", async () => {
    const { configure, authorize } = mockMusicKit();
    const counters = stubConnectRoutes();

    const { result } = renderHook(
      () => useAppleMusicConnect({ prewarm: true }),
      {
        wrapper: makeWrapper(),
      },
    );

    // Mount alone completes the setup chain — fetch then configure...
    await waitFor(() => expect(configure).toHaveBeenCalledTimes(1));
    expect(counters.configFetches).toBe(1);
    // ...but never opens Apple's sheet without a click.
    expect(authorize).not.toHaveBeenCalled();

    await act(async () => {
      await result.current.connect();
    });

    expect(authorize).toHaveBeenCalledTimes(1);
    // The click path reused the prewarmed setup — no second config fetch.
    expect(counters.configFetches).toBe(1);
  });

  it("does not prewarm when the flag is off", async () => {
    mockMusicKit();
    const counters = stubConnectRoutes();

    renderHook(() => useAppleMusicConnect(), { wrapper: makeWrapper() });

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(counters.configFetches).toBe(0);
  });

  it("holds a prewarm failure for the click — no toast on mount", async () => {
    const errorSpy = vi.spyOn(toasts, "error");
    const counters = { configFetches: 0 };
    server.use(
      http.get("*/api/v1/connectors/apple_music/musickit-config", () => {
        counters.configFetches += 1;
        return HttpResponse.json(
          { error: { code: "NOT_CONFIGURED", message: "no MusicKit key" } },
          { status: 500 },
        );
      }),
    );

    const { result } = renderHook(
      () => useAppleMusicConnect({ prewarm: true }),
      {
        wrapper: makeWrapper(),
      },
    );

    await waitFor(() => expect(counters.configFetches).toBeGreaterThan(0));
    expect(errorSpy).not.toHaveBeenCalled();

    await act(async () => {
      await result.current.connect();
    });

    expect(errorSpy).toHaveBeenCalledWith(
      "Failed to connect Apple Music",
      expect.anything(),
    );
  });
});

describe("authorize() rejection classification", () => {
  it("maps a blocked-popup rejection to the allow-popups toast, not the quiet cancel", async () => {
    const errorSpy = vi.spyOn(toasts, "error");
    const messageSpy = vi.spyOn(toasts, "message");
    mockMusicKit(
      vi
        .fn()
        .mockRejectedValue(new Error("The popup was blocked by the browser")),
    );
    stubConnectRoutes();

    const { result } = renderHook(() => useAppleMusicConnect(), {
      wrapper: makeWrapper(),
    });
    await act(async () => {
      await result.current.connect();
    });

    expect(errorSpy).toHaveBeenCalledWith(
      "Allow popups for this site, then try again",
      expect.anything(),
    );
    expect(messageSpy).not.toHaveBeenCalled();
  });

  it("treats MKError ACCESS_DENIED as a quiet dismissal", async () => {
    const errorSpy = vi.spyOn(toasts, "error");
    const messageSpy = vi.spyOn(toasts, "message");
    // No cancel-shaped message — only the errorCode identifies the dismissal.
    mockMusicKit(
      vi.fn().mockRejectedValue(
        Object.assign(new Error("Authorization request denied"), {
          errorCode: "ACCESS_DENIED",
        }),
      ),
    );
    stubConnectRoutes();

    const { result } = renderHook(() => useAppleMusicConnect(), {
      wrapper: makeWrapper(),
    });
    await act(async () => {
      await result.current.connect();
    });

    expect(messageSpy).toHaveBeenCalledWith("Apple Music connection canceled");
    expect(errorSpy).not.toHaveBeenCalled();
  });
});

describe("loadMusicKit hardening", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  const fakeStatic: MusicKitStatic = {
    configure: () => Promise.resolve(undefined),
    getInstance: () => ({ authorize: () => Promise.resolve("mut") }),
  };

  /** Fresh module instance → fresh module-level promise cache. */
  async function freshLoader() {
    vi.resetModules();
    const mod = await import("./useAppleMusicConnect");
    return mod.loadMusicKit;
  }

  it("clears the cache when musickitloaded fires without the global, so retry works", async () => {
    const load = await freshLoader();

    const first = load();
    const firstRejects = expect(first).rejects.toThrow(
      "MusicKit JS loaded without window.MusicKit",
    );
    document.dispatchEvent(new Event("musickitloaded"));
    await firstRejects;

    // A fresh attempt (not the dead cached promise) can now succeed.
    const second = load();
    window.MusicKit = fakeStatic;
    document.dispatchEvent(new Event("musickitloaded"));
    await expect(second).resolves.toBe(fakeStatic);
  });

  it("times out a hung load, rejects, and clears the cache for retry", async () => {
    vi.useFakeTimers();
    const load = await freshLoader();

    const first = load();
    const firstRejects = expect(first).rejects.toThrow(
      "Timed out loading MusicKit JS",
    );
    vi.advanceTimersByTime(15_000);
    await firstRejects;

    const second = load();
    window.MusicKit = fakeStatic;
    document.dispatchEvent(new Event("musickitloaded"));
    await expect(second).resolves.toBe(fakeStatic);
  });
});
