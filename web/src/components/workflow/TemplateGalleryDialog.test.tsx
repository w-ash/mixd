import { HttpResponse, http } from "msw";
import { Route, Routes, useParams } from "react-router";
import { describe, expect, it } from "vitest";

import { Button } from "#/components/ui/button";
import { Toaster } from "#/components/ui/sonner";
import { server } from "#/test/setup";
import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
} from "#/test/test-utils";

import { TemplateGalleryDialog } from "./TemplateGalleryDialog";

const NEW_ID = "33333333-3333-3333-3333-333333333333";

/** Stands in for the editor route so the test can see where Use lands. */
function EditorRouteProbe() {
  const { id } = useParams();
  return <p>Editor for {id}</p>;
}

function renderGallery() {
  return renderWithProviders(
    <TemplateGalleryDialog trigger={<Button>From template</Button>} />,
  );
}

const templates = [
  {
    id: "current_obsessions",
    name: "Current Obsessions",
    description: "Tracks with 8+ plays in last 30 days",
    task_count: 4,
    node_types: [
      "source.liked_tracks",
      "filter.play_count",
      "destination.playlist",
    ],
  },
  {
    id: "fresh_finds",
    name: "Fresh Finds",
    description: "Recently added, never played",
    task_count: 2,
    node_types: ["source.liked_tracks", "destination.playlist"],
  },
];

describe("TemplateGalleryDialog", () => {
  it("lists templates from the gallery endpoint when opened", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("*/api/v1/workflows/templates", () =>
        HttpResponse.json(templates, { status: 200 }),
      ),
    );

    renderGallery();

    await user.click(screen.getByRole("button", { name: "From template" }));

    await waitFor(() => {
      expect(screen.getByText("Current Obsessions")).toBeInTheDocument();
    });
    expect(screen.getByText("Fresh Finds")).toBeInTheDocument();
  });

  it("instantiates the chosen template, toasts, and opens the new workflow in the editor", async () => {
    const user = userEvent.setup();
    let usedTemplateId: string | null = null;

    server.use(
      http.get("*/api/v1/workflows/templates", () =>
        HttpResponse.json(templates, { status: 200 }),
      ),
      http.post(
        "*/api/v1/workflows/templates/:templateId/use",
        ({ params }) => {
          usedTemplateId = params.templateId as string;
          return HttpResponse.json(
            {
              id: NEW_ID,
              name: "Current Obsessions",
              description: null,
              definition_version: 1,
              task_count: 4,
              node_types: ["source.liked_tracks"],
              updated_at: "2026-05-30T00:00:00Z",
              definition: {
                id: NEW_ID,
                name: "Current Obsessions",
                description: "",
                version: "1.0",
                tasks: [],
              },
            },
            { status: 201 },
          );
        },
      ),
    );

    renderWithProviders(
      <>
        <Routes>
          <Route
            path="/workflows"
            element={
              <TemplateGalleryDialog trigger={<Button>From template</Button>} />
            }
          />
          <Route path="/workflows/:id/edit" element={<EditorRouteProbe />} />
        </Routes>
        <Toaster />
      </>,
      { routerProps: { initialEntries: ["/workflows"] } },
    );

    await user.click(screen.getByRole("button", { name: "From template" }));
    await user.click(await screen.findByText("Current Obsessions"));

    expect(await screen.findByText(`Editor for ${NEW_ID}`)).toBeInTheDocument();
    expect(usedTemplateId).toBe("current_obsessions");
    expect(
      await screen.findByText("Workflow created from template"),
    ).toBeInTheDocument();
  });

  it("shows an empty state when the gallery has no templates", async () => {
    const user = userEvent.setup();
    server.use(
      http.get("*/api/v1/workflows/templates", () =>
        HttpResponse.json([], { status: 200 }),
      ),
    );

    renderGallery();

    await user.click(screen.getByRole("button", { name: "From template" }));

    await waitFor(() => {
      expect(screen.getByText("No templates available")).toBeInTheDocument();
    });
  });
});
