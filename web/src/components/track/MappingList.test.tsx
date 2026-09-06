import { describe, expect, it } from "vitest";

import type { ConnectorMappingSchema } from "#/api/generated/model";
import { renderWithProviders, screen } from "#/test/test-utils";

import { MappingList } from "./MappingList";

function makeMapping(
  overrides: Partial<ConnectorMappingSchema> = {},
): ConnectorMappingSchema {
  return {
    mapping_id: "m1",
    connector_name: "spotify",
    connector_track_id: "sp-123",
    connector_track_title: "Paranoid Android",
    connector_track_artists: ["Radiohead"],
    match_method: "direct_import",
    confidence: 100,
    origin: "automatic",
    is_primary: true,
    external_url: "https://open.spotify.com/track/sp-123",
    ...overrides,
  };
}

describe("MappingList", () => {
  it("renders an Open link from mapping.external_url when present", () => {
    renderWithProviders(
      <MappingList
        trackId="t1"
        trackTitle="Paranoid Android"
        mappings={[makeMapping()]}
      />,
    );

    const link = screen.getByRole("link", { name: /Open/ });
    expect(link).toHaveAttribute(
      "href",
      "https://open.spotify.com/track/sp-123",
    );
  });

  it("omits the Open link when mapping.external_url is null", () => {
    renderWithProviders(
      <MappingList
        trackId="t1"
        trackTitle="Some Track"
        mappings={[
          makeMapping({
            connector_name: "lastfm",
            connector_track_id: "lf-456",
            external_url: null,
          }),
        ]}
      />,
    );

    expect(
      screen.queryByRole("link", { name: /Open/ }),
    ).not.toBeInTheDocument();
  });
});
