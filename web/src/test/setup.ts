import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll } from "vitest";

// jsdom polyfills — APIs missing from jsdom that components rely on
globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};
Element.prototype.scrollIntoView = () => {};
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

import { getAuthMock } from "#/api/generated/auth/auth.msw";
import { getConnectorsMock } from "#/api/generated/connectors/connectors.msw";
import { getHealthMock } from "#/api/generated/health/health.msw";
import { getImportsMock } from "#/api/generated/imports/imports.msw";
import { getOperationsMock } from "#/api/generated/operations/operations.msw";
import { getPlaylistAssignmentsMock } from "#/api/generated/playlist-assignments/playlist-assignments.msw";
import { getPlaylistsMock } from "#/api/generated/playlists/playlists.msw";
import { getSettingsMock } from "#/api/generated/settings/settings.msw";
import { getStatsMock } from "#/api/generated/stats/stats.msw";
import { getTracksMock } from "#/api/generated/tracks/tracks.msw";
import { getWorkflowsMock } from "#/api/generated/workflows/workflows.msw";

export const server = setupServer(
  ...getAuthMock(),
  ...getPlaylistsMock(),
  ...getTracksMock(),
  ...getConnectorsMock(),
  ...getHealthMock(),
  ...getImportsMock(),
  ...getOperationsMock(),
  ...getPlaylistAssignmentsMock(),
  ...getSettingsMock(),
  ...getStatsMock(),
  ...getWorkflowsMock(),
);

beforeAll(() => server.listen({ onUnhandledRequest: "warn" }));
afterEach(() => {
  server.resetHandlers();
  cleanup();
});
afterAll(() => server.close());
