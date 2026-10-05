import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { server } from "#/test/setup";
import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
} from "#/test/test-utils";

import { UnlinkMappingDialog } from "./UnlinkMappingDialog";

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
    <UnlinkMappingDialog
      trackId="019d0000-0000-7000-8000-000000000042"
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
    expect(screen.getByText(/orphan track/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Unlink" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  });

  it("does not render content when closed", () => {
    renderDialog(false);

    expect(screen.queryByText("Unlink Mapping")).not.toBeInTheDocument();
  });

  it("confirming deletes this mapping from this track and closes", async () => {
    const deleted: Array<{ trackId: string; mappingId: string }> = [];
    server.use(
      http.delete(
        "*/api/v1/tracks/:trackId/mappings/:mappingId",
        ({ params }) => {
          deleted.push({
            trackId: String(params.trackId),
            mappingId: String(params.mappingId),
          });
          return HttpResponse.json(
            { deleted_mapping_id: String(params.mappingId) },
            { status: 200 },
          );
        },
      ),
    );
    const onOpenChange = vi.fn();
    renderWithProviders(
      <UnlinkMappingDialog
        trackId="019d0000-0000-7000-8000-000000000042"
        mapping={mockMapping}
        open
        onOpenChange={onOpenChange}
      />,
    );

    await userEvent.click(
      await screen.findByRole("button", { name: "Unlink" }),
    );

    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(deleted).toEqual([
      {
        trackId: "019d0000-0000-7000-8000-000000000042",
        mappingId: "019d0000-0000-7000-8000-000000000010",
      },
    ]);
  });
});
