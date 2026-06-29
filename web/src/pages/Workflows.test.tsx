import { fireEvent } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

// useNavigate is mocked so the list-page Import can be asserted to open the
// editor; everything else in react-router stays real (Link, MemoryRouter).
const { mockNavigate } = vi.hoisted(() => ({ mockNavigate: vi.fn() }));
vi.mock("react-router", async (importActual) => {
  const actual = await importActual<typeof import("react-router")>();
  return { ...actual, useNavigate: () => mockNavigate };
});

// Import seeds the editor store, whose loadWorkflow runs an async ELK layout —
// mock it so the seed is synchronous and deterministic.
vi.mock("#/lib/workflow-layout", () => ({
  layoutWorkflow: vi.fn().mockResolvedValue({ nodes: [], edges: [] }),
  buildEdges: vi.fn().mockReturnValue({ flowEdges: [] }),
  generateNodeId: vi.fn().mockReturnValue("node_1"),
}));

import { toasts } from "#/lib/toasts";
import { useEditorStore } from "#/stores/editor-store";
import { server } from "#/test/setup";
import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
} from "#/test/test-utils";

import { Workflows } from "./Workflows";

function fileFromDef(def: unknown): File {
  return new File([JSON.stringify(def)], "wf.json", {
    type: "application/json",
  });
}

const ONE_ROW = [
  {
    id: "11111111-1111-1111-1111-111111111111",
    name: "Flow A",
    description: null,
    definition_version: 1,
    task_count: 2,
    node_types: ["source"],
    updated_at: "2026-02-15T12:00:00Z",
  },
];

const WF_A = "11111111-1111-1111-1111-111111111111";
const WF_B = "22222222-2222-2222-2222-222222222222";

function listResponse(data: unknown[]) {
  return HttpResponse.json(
    { data, total: data.length, limit: 50, offset: 0 },
    { status: 200 },
  );
}

describe("Workflows", () => {
  it("renders loading skeleton initially", () => {
    renderWithProviders(<Workflows />);

    const skeletons = document.querySelectorAll('[data-slot="skeleton"]');
    expect(skeletons.length).toBeGreaterThan(0);
  });

  it("renders workflow table when API returns data", async () => {
    server.use(
      http.get("*/api/v1/workflows", () =>
        listResponse([
          {
            id: WF_A,
            name: "Current Obsessions",
            description: "Tracks with 8+ plays in last 30 days",
            definition_version: 3,
            task_count: 4,
            node_types: [
              "source.liked_tracks",
              "filter.play_count",
              "sorter.play_count",
              "destination.playlist",
            ],
            updated_at: "2026-02-15T12:00:00Z",
            last_run: {
              id: 1,
              status: "completed",
              definition_version: 3,
              completed_at: "2026-02-15T11:00:00Z",
              output_track_count: 20,
            },
          },
          {
            id: WF_B,
            name: "My Custom Flow",
            description: null,
            definition_version: 1,
            task_count: 2,
            node_types: ["source.liked_tracks", "destination.playlist"],
            updated_at: "2026-03-01T08:30:00Z",
          },
        ]),
      ),
    );

    renderWithProviders(<Workflows />);

    await waitFor(() => {
      expect(screen.getAllByText("Current Obsessions").length).toBeGreaterThan(
        0,
      );
    });

    expect(screen.getAllByText("My Custom Flow").length).toBeGreaterThan(0);
    expect(screen.getByText("4")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("shows no Template badge — templates live in the gallery, not the list", async () => {
    server.use(
      http.get("*/api/v1/workflows", () =>
        listResponse([
          {
            id: WF_A,
            name: "Current Obsessions",
            description: null,
            definition_version: 1,
            task_count: 3,
            node_types: ["source.liked_tracks"],
            updated_at: "2026-02-15T12:00:00Z",
          },
        ]),
      ),
    );

    renderWithProviders(<Workflows />);

    await waitFor(() => {
      expect(screen.getAllByText("Current Obsessions").length).toBeGreaterThan(
        0,
      );
    });

    expect(screen.queryByText("Template")).not.toBeInTheDocument();
  });

  it("shows last run status in the table", async () => {
    server.use(
      http.get("*/api/v1/workflows", () =>
        listResponse([
          {
            id: WF_A,
            name: "Current Obsessions",
            description: null,
            definition_version: 1,
            task_count: 3,
            node_types: ["source"],
            updated_at: "2026-02-15T12:00:00Z",
            last_run: {
              id: 5,
              status: "completed",
              definition_version: 1,
            },
          },
        ]),
      ),
    );

    renderWithProviders(<Workflows />);

    await waitFor(() => {
      expect(screen.getAllByText("Completed").length).toBeGreaterThan(0);
    });
  });

  it("renders per-row run, edit, and duplicate actions on every row", async () => {
    server.use(
      http.get("*/api/v1/workflows", () =>
        listResponse([
          {
            id: WF_A,
            name: "Flow A",
            description: null,
            definition_version: 1,
            task_count: 2,
            node_types: ["source"],
            updated_at: "2026-02-15T12:00:00Z",
          },
        ]),
      ),
    );

    renderWithProviders(<Workflows />);

    await waitFor(() => {
      expect(screen.getAllByText("Flow A").length).toBeGreaterThan(0);
    });

    expect(screen.getAllByTitle("Run workflow").length).toBeGreaterThan(0);
    expect(screen.getAllByTitle("Edit workflow").length).toBeGreaterThan(0);
    expect(screen.getAllByTitle("Duplicate workflow").length).toBeGreaterThan(
      0,
    );
  });

  it("duplicates a workflow when its row Duplicate action is clicked", async () => {
    const user = userEvent.setup();
    let duplicatedId: string | null = null;

    server.use(
      http.get("*/api/v1/workflows", () =>
        listResponse([
          {
            id: WF_A,
            name: "Flow A",
            description: null,
            definition_version: 1,
            task_count: 2,
            node_types: ["source"],
            updated_at: "2026-02-15T12:00:00Z",
          },
        ]),
      ),
      http.post("*/api/v1/workflows/:id/duplicate", ({ params }) => {
        duplicatedId = params.id as string;
        return HttpResponse.json(
          {
            id: WF_B,
            name: "Flow A (copy)",
            description: null,
            definition_version: 1,
            task_count: 2,
            node_types: ["source"],
            updated_at: "2026-03-01T08:30:00Z",
            definition: {
              id: WF_B,
              name: "Flow A (copy)",
              description: "",
              version: "1.0",
              tasks: [],
            },
          },
          { status: 201 },
        );
      }),
    );

    renderWithProviders(<Workflows />);

    await waitFor(() => {
      expect(screen.getAllByText("Flow A").length).toBeGreaterThan(0);
    });

    await user.click(screen.getAllByTitle("Duplicate workflow")[0]);

    await waitFor(() => {
      expect(duplicatedId).toBe(WF_A);
    });
  });

  it("renders empty state when API returns no workflows", async () => {
    server.use(http.get("*/api/v1/workflows", () => listResponse([])));

    renderWithProviders(<Workflows />);

    await waitFor(() => {
      expect(screen.getByText("No workflows yet")).toBeInTheDocument();
    });
  });

  it("imports a valid workflow file and opens the editor on the draft", async () => {
    mockNavigate.mockClear();
    useEditorStore.setState({ workflowName: "Old", workflowId: "existing" });
    server.use(http.get("*/api/v1/workflows", () => listResponse(ONE_ROW)));

    renderWithProviders(<Workflows />);
    await waitFor(() => {
      expect(screen.getAllByText("Flow A").length).toBeGreaterThan(0);
    });

    const input = screen.getByLabelText(
      "Import workflow file",
    ) as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [fileFromDef({ name: "Imported", tasks: [] })] },
    });

    await waitFor(() => {
      // `imported: true` tells WorkflowEditor not to reset the seeded draft.
      expect(mockNavigate).toHaveBeenCalledWith("/workflows/new", {
        state: { imported: true },
      });
    });
    expect(useEditorStore.getState().workflowName).toBe("Imported");
    expect(useEditorStore.getState().workflowId).toBeNull();
  });

  it("shows an error toast and does not navigate on an invalid file", async () => {
    mockNavigate.mockClear();
    const errorSpy = vi.spyOn(toasts, "error").mockImplementation(() => {});
    server.use(http.get("*/api/v1/workflows", () => listResponse(ONE_ROW)));

    renderWithProviders(<Workflows />);
    await waitFor(() => {
      expect(screen.getAllByText("Flow A").length).toBeGreaterThan(0);
    });

    const input = screen.getByLabelText(
      "Import workflow file",
    ) as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [new File(["not json {"], "bad.json")] },
    });

    await waitFor(() => {
      expect(errorSpy).toHaveBeenCalledWith(
        "Couldn't import workflow",
        expect.any(Error),
      );
    });
    expect(mockNavigate).not.toHaveBeenCalled();
    errorSpy.mockRestore();
  });

  it("renders error state when API fails", async () => {
    server.use(
      http.get("*/api/v1/workflows", () =>
        HttpResponse.json(
          { error: { code: "INTERNAL_ERROR", message: "Server error" } },
          { status: 500 },
        ),
      ),
    );

    renderWithProviders(<Workflows />);

    await waitFor(() => {
      expect(screen.getByText("Failed to load workflows")).toBeInTheDocument();
    });
  });
});
