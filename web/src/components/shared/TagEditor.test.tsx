import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { server } from "#/test/setup";
import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
} from "#/test/test-utils";

import { TagEditor } from "./TagEditor";

describe("TagEditor", () => {
  it("renders a chip for every value", () => {
    server.use(http.get("*/api/v1/tags", () => HttpResponse.json([])));

    renderWithProviders(
      <TagEditor
        value={["mood:chill", "banger"]}
        onAdd={vi.fn()}
        onRemove={vi.fn()}
      />,
    );

    expect(screen.getByText("mood:chill")).toBeInTheDocument();
    expect(screen.getByText("banger")).toBeInTheDocument();
  });

  it("calls onRemove when a chip × is clicked", async () => {
    server.use(http.get("*/api/v1/tags", () => HttpResponse.json([])));

    const onRemove = vi.fn();
    renderWithProviders(
      <TagEditor value={["mood:chill"]} onAdd={vi.fn()} onRemove={onRemove} />,
    );

    await userEvent.click(
      screen.getByRole("button", { name: "Remove mood:chill" }),
    );

    expect(onRemove).toHaveBeenCalledWith("mood:chill");
  });

  it("routes an autocomplete selection to onAdd", async () => {
    server.use(
      http.get("*/api/v1/tags", () =>
        HttpResponse.json([{ tag: "energy:high", count: 3 }]),
      ),
    );

    const onAdd = vi.fn();
    renderWithProviders(
      <TagEditor value={[]} onAdd={onAdd} onRemove={vi.fn()} />,
    );

    await waitFor(() => {
      expect(screen.getByText("energy:high")).toBeInTheDocument();
    });

    await userEvent.click(screen.getByText("energy:high"));

    expect(onAdd).toHaveBeenCalledWith("energy:high");
  });

  it("hides the autocomplete when disabled", () => {
    server.use(http.get("*/api/v1/tags", () => HttpResponse.json([])));

    renderWithProviders(
      <TagEditor
        value={["mood:chill"]}
        onAdd={vi.fn()}
        onRemove={vi.fn()}
        disabled
      />,
    );

    expect(screen.queryByPlaceholderText("Add a tag…")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /remove/i }),
    ).not.toBeInTheDocument();
  });
});
