import { describe, expect, it } from "vitest";

import { renderWithProviders, screen } from "#/test/test-utils";

import { PlaylistMeta } from "./PlaylistMeta";

describe("PlaylistMeta", () => {
  it("renders the owner and a thousand-separated track count", () => {
    renderWithProviders(<PlaylistMeta owner="dj-ash" trackCount={1247} />);

    expect(screen.getByText(/dj-ash/)).toBeInTheDocument();
    expect(screen.getByText("1,247")).toBeInTheDocument();
    expect(screen.getByText(/tracks/)).toBeInTheDocument();
  });

  it("falls back to Unknown when the connector omits the owner", () => {
    renderWithProviders(<PlaylistMeta owner={null} trackCount={0} />);

    expect(screen.getByText(/Unknown/)).toBeInTheDocument();
    expect(screen.getByText("0")).toBeInTheDocument();
  });
});
