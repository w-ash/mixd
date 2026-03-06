import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll } from "vitest";

import { getConnectorsMock } from "@/api/generated/connectors/connectors.msw";
import { getHealthMock } from "@/api/generated/health/health.msw";
import { getImportsMock } from "@/api/generated/imports/imports.msw";
import { getOperationsMock } from "@/api/generated/operations/operations.msw";
import { getPlaylistsMock } from "@/api/generated/playlists/playlists.msw";
import { getStatsMock } from "@/api/generated/stats/stats.msw";
import { getTracksMock } from "@/api/generated/tracks/tracks.msw";

export const server = setupServer(
  ...getPlaylistsMock(),
  ...getTracksMock(),
  ...getConnectorsMock(),
  ...getHealthMock(),
  ...getImportsMock(),
  ...getOperationsMock(),
  ...getStatsMock(),
);

beforeAll(() => server.listen({ onUnhandledRequest: "warn" }));
afterEach(() => {
  server.resetHandlers();
  cleanup();
});
afterAll(() => server.close());
