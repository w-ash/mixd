import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { DirectionChooser } from "./DirectionChooser";
import { SyncDirectionIndicator } from "./SyncDirectionIndicator";

describe("DirectionChooser", () => {
  it("renders both directions leading with what gets overwritten", () => {
    render(
      <DirectionChooser
        value="pull"
        onChange={() => {}}
        connectorLabel="Spotify"
      />,
    );
    expect(
      screen.getByText("Spotify → Mixd (replaces Mixd)"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Mixd → Spotify (replaces Spotify)"),
    ).toBeInTheDocument();
  });

  it("marks the selected direction checked", () => {
    render(
      <DirectionChooser
        value="push"
        onChange={() => {}}
        connectorLabel="Spotify"
      />,
    );
    expect(screen.getByRole("radio", { name: /Mixd → Spotify/ })).toBeChecked();
    expect(
      screen.getByRole("radio", { name: /Spotify → Mixd/ }),
    ).not.toBeChecked();
  });

  it("calls onChange with the picked direction", async () => {
    const onChange = vi.fn();
    render(
      <DirectionChooser
        value="pull"
        onChange={onChange}
        connectorLabel="Spotify"
      />,
    );
    await userEvent.click(
      screen.getByRole("radio", { name: /Mixd → Spotify/ }),
    );
    expect(onChange).toHaveBeenCalledWith("push");
  });
});

describe("SyncDirectionIndicator", () => {
  it("prefers the API label when provided", () => {
    render(
      <SyncDirectionIndicator
        direction="pull"
        connectorLabel="Spotify"
        label="Spotify → Mixd (replaces Mixd)"
      />,
    );
    expect(
      screen.getByText("Spotify → Mixd (replaces Mixd)"),
    ).toBeInTheDocument();
  });

  it("derives the label from direction when none is given", () => {
    render(
      <SyncDirectionIndicator direction="push" connectorLabel="Spotify" />,
    );
    expect(
      screen.getByText("Mixd → Spotify (replaces Spotify)"),
    ).toBeInTheDocument();
  });
});
