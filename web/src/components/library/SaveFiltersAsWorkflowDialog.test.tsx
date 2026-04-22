import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { server } from "#/test/setup";
import { renderWithProviders } from "#/test/test-utils";

import { SaveFiltersAsWorkflowDialog } from "./SaveFiltersAsWorkflowDialog";

const mockNavigate = vi.fn();
vi.mock("react-router", async () => {
  const actual =
    await vi.importActual<typeof import("react-router")>("react-router");
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

const mockToastSuccess = vi.fn();
vi.mock("#/lib/toasts", async () => {
  const actual =
    await vi.importActual<typeof import("#/lib/toasts")>("#/lib/toasts");
  return {
    ...actual,
    toasts: {
      ...actual.toasts,
      success: (...args: unknown[]) => mockToastSuccess(...args),
    },
  };
});

describe("SaveFiltersAsWorkflowDialog", () => {
  it("disables Save until a name is entered", async () => {
    renderWithProviders(
      <SaveFiltersAsWorkflowDialog
        open
        onOpenChange={vi.fn()}
        filters={{ preference: "star" }}
      />,
    );

    const save = screen.getByRole("button", { name: /Save & open editor/ });
    expect(save).toBeDisabled();

    await userEvent.type(screen.getByLabelText("Name"), "My Mix");
    expect(save).not.toBeDisabled();
  });

  it("POSTs the serialized workflow and navigates to the editor on success", async () => {
    mockNavigate.mockReset();
    const capturedBody: unknown[] = [];
    server.use(
      http.post("*/api/v1/workflows", async ({ request }) => {
        capturedBody.push(await request.json());
        return HttpResponse.json(
          {
            id: "wf_new_123",
            name: "My Mix",
            is_template: false,
            definition: { id: "my_mix", name: "My Mix", tasks: [] },
          },
          { status: 201 },
        );
      }),
    );

    const onOpenChange = vi.fn();
    renderWithProviders(
      <SaveFiltersAsWorkflowDialog
        open
        onOpenChange={onOpenChange}
        filters={{ preference: "star", tags: ["mood:chill"], tagMode: "and" }}
      />,
    );

    await userEvent.type(screen.getByLabelText("Name"), "My Mix");
    await userEvent.click(
      screen.getByRole("button", { name: /Save & open editor/ }),
    );

    // The POST should fire with a serialized definition derived from the filter state.
    await vi.waitFor(() => {
      expect(capturedBody).toHaveLength(1);
    });
    const body = capturedBody[0] as {
      definition: { name: string; tasks: { type: string }[] };
    };
    expect(body.definition.name).toBe("My Mix");
    const taskTypes = body.definition.tasks.map((t) => t.type);
    expect(taskTypes).toContain("source.preferred_tracks");
    expect(taskTypes).toContain("filter.by_tag");

    // On success, dialog closes and navigation fires.
    await vi.waitFor(() => {
      expect(onOpenChange).toHaveBeenCalledWith(false);
      expect(mockNavigate).toHaveBeenCalledWith("/workflows/wf_new_123/edit");
      expect(mockToastSuccess).toHaveBeenCalledWith('Saved "My Mix"');
    });
  });

  it("shows an inline error on failed save and leaves the dialog open", async () => {
    server.use(
      http.post("*/api/v1/workflows", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );

    const onOpenChange = vi.fn();
    renderWithProviders(
      <SaveFiltersAsWorkflowDialog
        open
        onOpenChange={onOpenChange}
        filters={{ preference: "yah" }}
      />,
    );

    await userEvent.type(screen.getByLabelText("Name"), "Fail Mix");
    await userEvent.click(
      screen.getByRole("button", { name: /Save & open editor/ }),
    );

    await vi.waitFor(() => {
      expect(screen.getByText(/Couldn't save workflow/)).toBeInTheDocument();
    });
    // Dialog wasn't told to close.
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });

  it("surfaces the narrows-to-liked note when set", () => {
    renderWithProviders(
      <SaveFiltersAsWorkflowDialog
        open
        onOpenChange={vi.fn()}
        filters={{ tags: ["mood:chill"] }}
        narrowsToLiked
      />,
    );
    expect(
      screen.getByText(/start from your liked tracks/),
    ).toBeInTheDocument();
  });
});
