/**
 * The factory's default status must equal the backend's, row for row.
 *
 * `makeConnectorMetadata` is what most component tests hand the UI, so a status
 * it gets wrong is a whole suite testing the wrong branch. Driving every case
 * off the exported matrix means the backend rule and the test fixtures cannot
 * disagree without a red test on one side or the other.
 */

import { describe, expect, it } from "vitest";

import { makeConnectorMetadata } from "./factories";
import { expiredAt, STATUS_MATRIX, statusStateFor } from "./status-matrix";

/** A name absent from `connectorDefaults`, so the row's own auth method wins. */
const UNKNOWN = "not-a-real-connector";

describe("connector status matrix", () => {
  it("exports every row the factory can be asked for", () => {
    expect(STATUS_MATRIX.length).toBeGreaterThan(0);
  });

  it.each(STATUS_MATRIX)(
    "$auth_method / $auth_error / connected=$connected / expired=$expired → $status",
    (row) => {
      expect(
        statusStateFor({
          auth_method: row.auth_method,
          auth_error: row.auth_error,
          connected: row.connected,
          expired: row.expired,
        }),
      ).toBe(row.status);
    },
  );

  it("gives the factory the exported status for a connector's own defaults", () => {
    // Spotify's registry row is oauth, so the connected/disconnected arms are
    // the ones a component test actually lands on.
    for (const connected of [false, true]) {
      const metadata = makeConnectorMetadata({ name: "spotify", connected });
      expect(metadata.status).toBe(
        statusStateFor({
          auth_method: "oauth",
          auth_error: null,
          connected,
          expired: false,
        }),
      );
    }
  });

  it("throws rather than guessing when a combination has no exported row", () => {
    expect(() =>
      statusStateFor({
        auth_method: "passkey" as never,
        auth_error: null,
        connected: true,
        expired: false,
      }),
    ).toThrow(/export-status-matrix/);
  });

  it("reads an expiry in the past as expired, one in the future as not", () => {
    const now = Date.now() / 1000;
    expect(expiredAt(now - 60)).toBe(true);
    expect(expiredAt(now + 60)).toBe(false);
    expect(expiredAt(null)).toBe(false);
  });

  it("carries an expired token through to the factory's default status", () => {
    // The arm the old hand-written mirror had no branch for at all: a connected
    // oauth connector whose grant has aged out reads `expired`, not `connected`.
    const metadata = makeConnectorMetadata({
      name: "spotify",
      connected: true,
      token_expires_at: Math.floor(Date.now() / 1000) - 60,
    });
    expect(metadata.status).toBe("expired");
  });

  it("leaves a connector with no registry row on its fallback auth method", () => {
    const metadata = makeConnectorMetadata({ name: UNKNOWN });
    expect(metadata.status).toBe("public_api");
  });
});
