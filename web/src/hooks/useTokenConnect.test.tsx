/**
 * Tests for the generic BYO-token connect hook.
 *
 * Covers the ordering contract (`mutateAsync` stays open until the
 * connectors refetch lands, so the success toast never precedes the card it
 * describes) and the inline-error contract (a rejected token rejects with
 * the 400 on `connectError`, with no global toast).
 */
import { QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { useGetConnectorsApiV1ConnectorsGet } from "#/api/generated/connectors/connectors";
import { toasts } from "#/lib/toasts";
import { makeConnectorMetadata } from "#/test/factories";
import { server } from "#/test/setup";
import { createTestQueryClient } from "#/test/test-utils";

import { useTokenConnect } from "./useTokenConnect";

function makeWrapper() {
  const client = createTestQueryClient();
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

/**
 * The hook plus a connectors observer, the way a card mounts it — an
 * invalidation refetches only active queries, so without an observer there
 * is nothing for the ordering contract to wait on.
 */
function useProbe(service: string, displayName: string) {
  useGetConnectorsApiV1ConnectorsGet();
  return useTokenConnect(service, displayName);
}

describe("useTokenConnect", () => {
  it("PUTs to the given service and declares success after the refetch", async () => {
    const successSpy = vi.spyOn(toasts, "success");
    let putBody: unknown;
    let refetched = false;
    server.use(
      http.get("*/api/v1/connectors", () => {
        refetched = true;
        return HttpResponse.json([makeConnectorMetadata({ name: "discogs" })]);
      }),
      http.put("*/api/v1/connectors/discogs/token", async ({ request }) => {
        putBody = await request.json();
        return new HttpResponse(null, { status: 204 });
      }),
    );

    const { result } = renderHook(() => useProbe("discogs", "Discogs"), {
      wrapper: makeWrapper(),
    });
    // The mount fetch is not the one under test.
    await waitFor(() => expect(refetched).toBe(true));
    refetched = false;

    await result.current.connect("abc123token");

    expect(putBody).toEqual({ token: "abc123token" });
    // `awaitInvalidation`: the refetch has already landed by the time the
    // caller's await resolves, and the toast names the connector.
    expect(refetched).toBe(true);
    expect(successSpy).toHaveBeenCalledWith("Discogs connected");
  });

  it("rejects with the 400 on connectError and fires no global toast", async () => {
    const errorSpy = vi.spyOn(toasts, "error");
    server.use(
      http.put("*/api/v1/connectors/discogs/token", () =>
        HttpResponse.json(
          {
            error: {
              code: "DISCOGS_INVALID_TOKEN",
              message: "Discogs rejected the token",
            },
          },
          { status: 400 },
        ),
      ),
    );

    const { result } = renderHook(() => useProbe("discogs", "Discogs"), {
      wrapper: makeWrapper(),
    });

    await expect(result.current.connect("bad-token")).rejects.toThrow();

    await waitFor(() => {
      expect(result.current.connectError).toMatchObject({
        message: "Discogs rejected the token",
      });
    });
    expect(errorSpy).not.toHaveBeenCalled();

    // The form clears the error when the dialog closes.
    result.current.resetConnect();
    await waitFor(() => expect(result.current.connectError).toBeNull());
  });
});
