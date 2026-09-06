import { QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import type {
  ConnectorMetadataSchema,
  SyncTargetSchema,
} from "#/api/generated/model";
import { createTestQueryClient } from "#/test/query-utils";
import { server } from "#/test/setup";

import { useSyncTargetBlock } from "./useSyncTargetBlock";

function target(overrides: Partial<SyncTargetSchema> = {}): SyncTargetSchema {
  return {
    id: "spotify:plays",
    label: "Spotify recent plays",
    service: "spotify",
    self_managed: true,
    available: true,
    blocked_reason: null,
    ...overrides,
  };
}

function connector(
  overrides: Partial<ConnectorMetadataSchema> = {},
): ConnectorMetadataSchema {
  return {
    name: "spotify",
    display_name: "Spotify",
    category: "streaming",
    auth_method: "oauth",
    status: "connected",
    connected: true,
    capabilities: [],
    auth_error: null,
    ...overrides,
  };
}

function mockApis(
  targets: SyncTargetSchema[],
  connectors: ConnectorMetadataSchema[],
) {
  server.use(
    http.get("*/api/v1/sync/targets", () =>
      HttpResponse.json({ data: targets }),
    ),
    http.get("*/api/v1/connectors", () => HttpResponse.json(connectors)),
  );
}

function wrapper() {
  const queryClient = createTestQueryClient();
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}

function renderBlock() {
  return renderHook(() => useSyncTargetBlock("spotify:plays", "import").block, {
    wrapper: wrapper(),
  });
}

describe("useSyncTargetBlock", () => {
  it("blocks a revoked token the credential check still calls available", async () => {
    // The stored token is present and correctly scoped, so `/sync/targets` says
    // available; only the live probe knows the grant was revoked. Gating on the
    // target alone would enable a run that fails inside the importer.
    mockApis(
      [target()],
      [connector({ connected: false, auth_error: "refresh_failed" })],
    );

    const { result } = renderBlock();

    await waitFor(() =>
      expect(result.current?.text).toBe(
        "Connect Spotify in Integrations to import.",
      ),
    );
  });

  it("asks for a reconnect when the grant is missing a scope", async () => {
    // scope_missing leaves the connector connected — the remedy is a new grant,
    // not a first connection, so the copy and the colour both differ.
    mockApis(
      [target({ available: false, blocked_reason: "CONNECTOR_SCOPE_MISSING" })],
      [connector()],
    );

    const { result } = renderBlock();

    await waitFor(() =>
      expect(result.current?.text).toBe(
        "Reconnect Spotify in Integrations to grant recently-played access.",
      ),
    );
    expect(result.current?.className).toBe("text-status-expired");
  });

  it("does not block a connected, available target", async () => {
    mockApis(
      [
        target(),
        target({
          id: "lastfm:plays",
          service: "lastfm",
          available: false,
          blocked_reason: "CONNECTOR_NOT_CONNECTED",
        }),
      ],
      [connector()],
    );

    const { result } = renderHook(
      () => ({
        block: useSyncTargetBlock("spotify:plays", "import").block,
        // A blocked sibling proves both queries resolved, so the null above is a
        // verdict rather than an unanswered one.
        other: useSyncTargetBlock("lastfm:plays", "import").block,
      }),
      { wrapper: wrapper() },
    );

    await waitFor(() => expect(result.current.other).not.toBeNull());
    expect(result.current.block).toBeNull();
  });

  it("blocks nothing while the target list is still loading", () => {
    mockApis([target({ available: false })], [connector()]);

    const { result } = renderBlock();

    expect(result.current).toBeNull();
  });

  it("blocks nothing for a target the server does not list", async () => {
    mockApis(
      [target({ id: "lastfm:plays", service: "lastfm" })],
      [connector()],
    );

    const { result } = renderBlock();

    await waitFor(() => expect(result.current).toBeNull());
  });
});
