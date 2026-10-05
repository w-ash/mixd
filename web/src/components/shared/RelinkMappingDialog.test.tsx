import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { server } from "#/test/setup";
import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
} from "#/test/test-utils";

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

  it("moves the mapping to the picked track on confirm", async () => {
    const TARGET_ID = "019d0000-0000-7000-8000-000000000077";
    server.use(
      http.get("*/api/v1/tracks", () =>
        HttpResponse.json(
          {
            data: [
              {
                id: TARGET_ID,
                title: "Paranoid Android (Remastered)",
                artists: [{ name: "Radiohead" }],
                album: "OK Computer OKNOTOK",
                duration_ms: 387_000,
                isrc: null,
                connector_names: [],
                is_liked: false,
              },
            ],
            total: 1,
            limit: 10,
            offset: 0,
          },
          { status: 200 },
        ),
      ),
    );
    const patches: Array<{ path: string; body: unknown }> = [];
    server.use(
      http.patch(
        "*/api/v1/tracks/:trackId/mappings/:mappingId",
        async ({ params, request }) => {
          patches.push({
            path: `${params.trackId}/${params.mappingId}`,
            body: await request.json(),
          });
          return HttpResponse.json({}, { status: 200 });
        },
      ),
    );
    const onOpenChange = vi.fn();
    const user = userEvent.setup();
    renderWithProviders(
      <RelinkMappingDialog
        trackId="019d0000-0000-7000-8000-000000000042"
        mapping={mockMapping}
        open
        onOpenChange={onOpenChange}
      />,
    );

    await user.type(
      await screen.findByPlaceholderText("Search for the target track..."),
      "paranoid",
    );
    await user.click(await screen.findByText("Paranoid Android (Remastered)"));
    await user.click(screen.getByRole("button", { name: "Relink" }));

    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(patches).toEqual([
      {
        path: "019d0000-0000-7000-8000-000000000042/019d0000-0000-7000-8000-000000000010",
        body: { new_track_id: TARGET_ID },
      },
    ]);
  });
});
