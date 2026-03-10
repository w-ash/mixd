import { describe, expect, it } from "vitest";

import { renderWithProviders, screen } from "@/test/test-utils";

import { ConnectorListItem } from "./ConnectorListItem";

describe("ConnectorListItem", () => {
  it("renders connector icon for spotify", () => {
    renderWithProviders(
      <ConnectorListItem connectorName="spotify">
        <span>Track info</span>
      </ConnectorListItem>,
    );

    expect(screen.getByText("Spotify")).toBeInTheDocument();
    expect(screen.getByTitle("Spotify")).toBeInTheDocument();
  });

  it("renders connector icon for lastfm", () => {
    renderWithProviders(
      <ConnectorListItem connectorName="lastfm">
        <span>Track info</span>
      </ConnectorListItem>,
    );

    expect(screen.getByText("Last.fm")).toBeInTheDocument();
  });

  it("renders children content", () => {
    renderWithProviders(
      <ConnectorListItem connectorName="spotify">
        <span>Paranoid Android</span>
        <span>Radiohead</span>
      </ConnectorListItem>,
    );

    expect(screen.getByText("Paranoid Android")).toBeInTheDocument();
    expect(screen.getByText("Radiohead")).toBeInTheDocument();
  });

  it("renders actions when provided", () => {
    renderWithProviders(
      <ConnectorListItem
        connectorName="spotify"
        actions={<button type="button">Unlink</button>}
      >
        <span>Track info</span>
      </ConnectorListItem>,
    );

    expect(screen.getByRole("button", { name: "Unlink" })).toBeInTheDocument();
  });

  it("renders multiple actions", () => {
    renderWithProviders(
      <ConnectorListItem
        connectorName="spotify"
        actions={
          <>
            <button type="button">Relink</button>
            <button type="button">Unlink</button>
          </>
        }
      >
        <span>Track info</span>
      </ConnectorListItem>,
    );

    expect(screen.getByRole("button", { name: "Relink" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Unlink" })).toBeInTheDocument();
  });

  it("omits actions container when actions not provided", () => {
    const { container } = renderWithProviders(
      <ConnectorListItem connectorName="spotify">
        <span>Track info</span>
      </ConnectorListItem>,
    );

    // The outer div has the connector icon div and the content div, but no actions div
    const outerDiv = container.firstElementChild;
    // ConnectorIcon span + content div = 2 children (no actions div)
    expect(outerDiv?.children).toHaveLength(2);
  });

  it("renders with connector icon SVG", () => {
    const { container } = renderWithProviders(
      <ConnectorListItem connectorName="spotify">
        <span>Track info</span>
      </ConnectorListItem>,
    );

    expect(container.querySelector("svg")).toBeInTheDocument();
  });

  it("applies muted styling when muted prop is true", () => {
    const { container } = renderWithProviders(
      <ConnectorListItem connectorName="spotify" muted>
        <span>Track info</span>
      </ConnectorListItem>,
    );

    const outerDiv = container.firstElementChild as HTMLElement;
    expect(outerDiv.className).toContain("opacity-75");
  });

  it("does not apply muted styling by default", () => {
    const { container } = renderWithProviders(
      <ConnectorListItem connectorName="spotify">
        <span>Track info</span>
      </ConnectorListItem>,
    );

    const outerDiv = container.firstElementChild as HTMLElement;
    expect(outerDiv.className).not.toContain("opacity-75");
  });
});
