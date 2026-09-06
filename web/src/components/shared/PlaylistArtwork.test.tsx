import { describe, expect, it } from "vitest";

import { renderWithProviders } from "#/test/test-utils";

import { PlaylistArtwork } from "./PlaylistArtwork";

describe("PlaylistArtwork", () => {
  it("renders a decorative, lazy-loaded cover when a url is given", () => {
    const { container } = renderWithProviders(
      <PlaylistArtwork src="https://cdn.example/cover.jpg" />,
    );

    const img = container.querySelector("img");
    expect(img).toHaveAttribute("src", "https://cdn.example/cover.jpg");
    expect(img).toHaveAttribute("alt", "");
    expect(img).toHaveAttribute("loading", "lazy");
  });

  it("renders a placeholder hidden from assistive tech when the url is missing", () => {
    const { container } = renderWithProviders(
      <PlaylistArtwork src={null} className="rounded-full" />,
    );

    expect(container.querySelector("img")).toBeNull();
    const placeholder = container.querySelector("[aria-hidden='true']");
    expect(placeholder).toHaveClass("bg-surface-muted", "rounded-full");
  });
});
