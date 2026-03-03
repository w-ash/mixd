import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll } from "vitest";

import { getConnectorsMock } from "@/api/generated/connectors/connectors.msw";
import { getHealthMock } from "@/api/generated/health/health.msw";
import { getPlaylistsMock } from "@/api/generated/playlists/playlists.msw";

export const server = setupServer(
  ...getPlaylistsMock(),
  ...getConnectorsMock(),
  ...getHealthMock(),
);

beforeAll(() => server.listen({ onUnhandledRequest: "warn" }));
afterEach(() => {
  server.resetHandlers();
  cleanup();
});
afterAll(() => server.close());
