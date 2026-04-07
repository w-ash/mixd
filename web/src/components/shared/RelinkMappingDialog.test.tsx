import { describe, expect, it } from "vitest";

import { renderWithProviders, screen, waitFor } from "#/test/test-utils";

import { RelinkMappingDialog } from "./RelinkMappingDialog";

const mockMapping = {
  mapping_id: "019d0000-0000-7000-8000-000000000010",
  connector_name: "spotify",
  connector_track_id: "sp-123",
  match_method: "direct_import",
  confidence: 100,
  origin: "automatic",
  is_primary: true,
  connector_track_title: "Paranoid Android",
  connector_track_artists: ["Radiohead"],
};

function renderDialog(open = true) {
  return renderWithProviders(
    <RelinkMappingDialog
      trackId="019d0000-0000-7000-8000-000000000042"
      mapping={mockMapping}
      open={open}
      onOpenChange={() => {}}
    />,
  );
}

describe("RelinkMappingDialog", () => {
  it("renders dialog with mapping info when open", async () => {
    renderDialog();

    await waitFor(() => {
      expect(screen.getByText("Relink Mapping")).toBeInTheDocument();
    });

    expect(screen.getByText("Paranoid Android")).toBeInTheDocument();
    expect(
      screen.getByPlaceholderText("Search for the target track..."),
    ).toBeInTheDocument();
  });

  it("does not render content when closed", () => {
    renderDialog(false);

    expect(screen.queryByText("Relink Mapping")).not.toBeInTheDocument();
  });

  it("shows search combobox initially", async () => {
    renderDialog();

    await waitFor(() => {
      expect(screen.getByText("Relink Mapping")).toBeInTheDocument();
    });

    expect(
      screen.getByPlaceholderText("Search for the target track..."),
    ).toBeInTheDocument();
  });
});
