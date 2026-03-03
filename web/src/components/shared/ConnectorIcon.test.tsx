import { describe, expect, it } from "vitest";

import { renderWithProviders, screen } from "@/test/test-utils";

import { ConnectorIcon } from "./ConnectorIcon";

describe("ConnectorIcon", () => {
  it("renders Spotify connector with SVG logo", () => {
    renderWithProviders(<ConnectorIcon name="spotify" />);

    expect(screen.getByText("Spotify")).toBeInTheDocument();
    expect(screen.getByTitle("Spotify")).toBeInTheDocument();
    // SVG logo is present as a child of the wrapper span
    const wrapper = screen.getByTitle("Spotify");
    expect(wrapper.querySelector("svg")).toBeInTheDocument();
  });

  it("renders Last.fm connector", () => {
    renderWithProviders(<ConnectorIcon name="lastfm" />);

    expect(screen.getByText("Last.fm")).toBeInTheDocument();
  });

  it("renders Apple Music connector", () => {
    renderWithProviders(<ConnectorIcon name="apple" />);

    expect(screen.getByText("Apple Music")).toBeInTheDocument();
  });

  it("renders MusicBrainz connector", () => {
    renderWithProviders(<ConnectorIcon name="musicbrainz" />);

    expect(screen.getByText("MusicBrainz")).toBeInTheDocument();
    expect(screen.getByTitle("MusicBrainz")).toBeInTheDocument();
  });

  it("returns null for unknown connector", () => {
    const { container } = renderWithProviders(
      <ConnectorIcon name="unknown_service" />,
    );

    expect(container.firstChild).toBeNull();
  });
});
