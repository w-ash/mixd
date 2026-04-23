import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import type {
  TagOperationResult,
  TagSummarySchema,
} from "#/api/generated/model";
import { server } from "#/test/setup";
import { renderWithProviders, screen, userEvent } from "#/test/test-utils";

import { Tags } from "./Tags";

const sampleTags: TagSummarySchema[] = [
  {
    tag: "mood:chill",
    namespace: "mood",
    value: "chill",
    track_count: 12,
    last_used_at: "2026-04-01T10:00:00Z",
  },
  {
    tag: "banger",
    namespace: null,
    value: "banger",
    track_count: 3,
    last_used_at: "2026-03-15T10:00:00Z",
  },
];

function setupListMock(tags: TagSummarySchema[] = sampleTags) {
  server.use(http.get("*/api/v1/tags", () => HttpResponse.json(tags)));
}

describe("Tags settings page", () => {
  it("renders page header and search input", async () => {
    setupListMock();
    renderWithProviders(<Tags />);

    expect(await screen.findByText("Tags")).toBeInTheDocument();
    expect(screen.getByLabelText("Filter tags")).toBeInTheDocument();
  });

  it("renders the tag table with namespace, count, and last-used", async () => {
    setupListMock();
    renderWithProviders(<Tags />);

    expect(await screen.findByText("mood:chill")).toBeInTheDocument();
    expect(screen.getByText("banger")).toBeInTheDocument();
    expect(screen.getByText("mood")).toBeInTheDocument();
    // Banger has no namespace.
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("shows empty state when no tags exist", async () => {
    setupListMock([]);
    renderWithProviders(<Tags />);

    expect(await screen.findByText("No tags yet")).toBeInTheDocument();
  });

  it("shows search-specific empty state when filter has no matches", async () => {
    let currentTags = sampleTags;
    server.use(
      http.get("*/api/v1/tags", ({ request }) => {
        const q = new URL(request.url).searchParams.get("q");
        if (q && q.length > 0) {
          currentTags = [];
        }
        return HttpResponse.json(currentTags);
      }),
    );
    renderWithProviders(<Tags />);

    await screen.findByText("mood:chill");
    const search = screen.getByLabelText("Filter tags");
    await userEvent.type(search, "nonexistent");

    expect(
      await screen.findByText('No tags matching "nonexistent"'),
    ).toBeInTheDocument();
  });

  it("opens rename dialog with affected-track count in the confirm button", async () => {
    setupListMock();
    renderWithProviders(<Tags />);

    await screen.findByText("mood:chill");
    await userEvent.click(screen.getByLabelText("Rename mood:chill"));

    expect(await screen.findByText("Rename tag")).toBeInTheDocument();
    expect(screen.getByText(/appears on 12 tracks/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Rename across 12 tracks/ }),
    ).toBeInTheDocument();
  });

  it("submits a rename and shows the affected count in the success toast", async () => {
    setupListMock();
    const renameSpy = vi.fn();
    server.use(
      http.patch("*/api/v1/tags/:tag", async ({ request }) => {
        const body = (await request.json()) as { new_tag: string };
        renameSpy(body.new_tag);
        const result: TagOperationResult = { affected_count: 12 };
        return HttpResponse.json(result);
      }),
    );
    renderWithProviders(<Tags />);

    await screen.findByText("mood:chill");
    await userEvent.click(screen.getByLabelText("Rename mood:chill"));
    const input = await screen.findByLabelText("New tag name");
    await userEvent.clear(input);
    await userEvent.type(input, "mood:ambient");
    await userEvent.click(
      screen.getByRole("button", { name: /Rename across 12 tracks/ }),
    );

    expect(renameSpy).toHaveBeenCalledWith("mood:ambient");
  });

  it("opens delete dialog with affected-track count in the description", async () => {
    setupListMock();
    renderWithProviders(<Tags />);

    await screen.findByText("mood:chill");
    await userEvent.click(screen.getByLabelText("Delete mood:chill"));

    expect(await screen.findByText("Delete tag")).toBeInTheDocument();
    expect(
      screen.getByText(/Removes "mood:chill" from 12 tracks/),
    ).toBeInTheDocument();
  });

  it("opens merge dialog and submits source/target to the API", async () => {
    setupListMock();
    const mergeSpy = vi.fn();
    server.use(
      http.post("*/api/v1/tags/merge", async ({ request }) => {
        const body = (await request.json()) as {
          source: string;
          target: string;
        };
        mergeSpy(body);
        const result: TagOperationResult = { affected_count: 12 };
        return HttpResponse.json(result);
      }),
    );
    renderWithProviders(<Tags />);

    await screen.findByText("mood:chill");
    await userEvent.click(
      screen.getByLabelText("Merge mood:chill into another tag"),
    );
    const input = await screen.findByLabelText("Target tag");
    await userEvent.type(input, "mood:ambient");
    await userEvent.click(
      screen.getByRole("button", { name: /Merge 12 tracks/ }),
    );

    expect(mergeSpy).toHaveBeenCalledWith({
      source: "mood:chill",
      target: "mood:ambient",
    });
  });
});
