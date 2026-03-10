import { describe, expect, it } from "vitest";

import { renderWithProviders, screen, waitFor } from "@/test/test-utils";

import { UnlinkMappingDialog } from "./UnlinkMappingDialog";

const mockMapping = {
  mapping_id: 10,
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
    <UnlinkMappingDialog
      trackId={42}
      mapping={mockMapping}
      open={open}
      onOpenChange={() => {}}
    />,
  );
}

describe("UnlinkMappingDialog", () => {
  it("renders dialog with mapping info when open", async () => {
    renderDialog();

    await waitFor(() => {
      expect(screen.getByText("Unlink Mapping")).toBeInTheDocument();
    });

    expect(screen.getByText("Paranoid Android")).toBeInTheDocument();
    expect(screen.getByText(/cannot be undone/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Unlink" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  });

  it("does not render content when closed", () => {
    renderDialog(false);

    expect(screen.queryByText("Unlink Mapping")).not.toBeInTheDocument();
  });

  it("shows orphan info in warning text", async () => {
    renderDialog();

    await waitFor(() => {
      expect(screen.getByText("Unlink Mapping")).toBeInTheDocument();
    });

    expect(screen.getByText(/orphan track/)).toBeInTheDocument();
  });
});
