import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { Button } from "#/components/ui/button";
import { server } from "#/test/setup";
import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
} from "#/test/test-utils";

import { TemplateGalleryDialog } from "./TemplateGalleryDialog";

const NEW_ID = "33333333-3333-3333-3333-333333333333";

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

  it("instantiates a template on Use and reports success", async () => {
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

    renderGallery();

    await user.click(screen.getByRole("button", { name: "From template" }));

    await waitFor(() => {
      expect(screen.getByText("Current Obsessions")).toBeInTheDocument();
    });

    await user.click(screen.getByText("Current Obsessions"));

    await waitFor(() => {
      expect(usedTemplateId).toBe("current_obsessions");
    });
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
