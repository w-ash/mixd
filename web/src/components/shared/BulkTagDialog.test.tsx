import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { server } from "#/test/setup";
import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
} from "#/test/test-utils";

const mockToastPromise = vi.fn();
vi.mock("#/lib/toasts", async () => {
  const actual =
    await vi.importActual<typeof import("#/lib/toasts")>("#/lib/toasts");
  return {
    ...actual,
    toasts: {
      ...actual.toasts,
      promise: (...args: unknown[]) => {
        mockToastPromise(...args);
        // Drive the underlying promise so onSuccess still fires.
        const promise = args[0] as Promise<unknown>;
        return promise;
      },
    },
  };
});

import { BulkTagDialog } from "./BulkTagDialog";

const trackIds = [
  "019d0000-0000-7000-8000-000000000001",
  "019d0000-0000-7000-8000-000000000002",
];

function setup(overrides: Partial<Parameters<typeof BulkTagDialog>[0]> = {}) {
  server.use(http.get("*/api/v1/tags", () => HttpResponse.json([])));
  const onOpenChange = vi.fn();
  const onTagged = vi.fn();
  renderWithProviders(
    <BulkTagDialog
      open={true}
      onOpenChange={onOpenChange}
      trackIds={trackIds}
      onTagged={onTagged}
      {...overrides}
    />,
  );
  return { onOpenChange, onTagged };
}

describe("BulkTagDialog", () => {
  it("renders the track count in the title and button", async () => {
    setup();
    await waitFor(() => {
      expect(
        screen.getByRole("heading", { name: "Tag 2 tracks" }),
      ).toBeInTheDocument();
    });
    expect(
      screen.getByRole("button", { name: "Tag 2 tracks" }),
    ).toBeInTheDocument();
  });

  it("submits the batch tag and reports success via onTagged + onOpenChange", async () => {
    server.use(
      http.get("*/api/v1/tags", () => HttpResponse.json([])),
      http.post("*/api/v1/tracks/tags/batch", () =>
        HttpResponse.json({ tag: "mood:chill", requested: 2, tagged: 2 }),
      ),
    );

    const { onOpenChange, onTagged } = setup();

    const input = screen.getByPlaceholderText("Pick or add a tag…");
    await userEvent.type(input, "mood:chill");

    await userEvent.click(await screen.findByText("mood:chill"));

    await userEvent.click(screen.getByRole("button", { name: "Tag 2 tracks" }));

    await waitFor(() => {
      expect(onTagged).toHaveBeenCalledOnce();
    });
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("disables the confirm button until a tag is chosen", async () => {
    setup();
    const btn = await screen.findByRole("button", { name: "Tag 2 tracks" });
    expect(btn).toBeDisabled();
  });

  it("closes without submitting on cancel", async () => {
    const { onOpenChange } = setup();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("wraps the mutation in toasts.promise with a loading message", async () => {
    mockToastPromise.mockClear();
    server.use(
      http.get("*/api/v1/tags", () => HttpResponse.json([])),
      http.post("*/api/v1/tracks/tags/batch", () =>
        HttpResponse.json({ tag: "mood:chill", requested: 2, tagged: 2 }),
      ),
    );

    setup();
    const input = screen.getByPlaceholderText("Pick or add a tag…");
    await userEvent.type(input, "mood:chill");
    await userEvent.click(await screen.findByText("mood:chill"));
    await userEvent.click(screen.getByRole("button", { name: "Tag 2 tracks" }));

    await waitFor(() => expect(mockToastPromise).toHaveBeenCalledOnce());
    const messages = mockToastPromise.mock.calls[0][1] as {
      loading: string;
      success: (resp: unknown) => string;
      error: string;
    };
    expect(messages.loading).toBe("Tagging 2 tracks…");
    expect(messages.error).toBe("Failed to tag tracks");
    // Success template renders the affected count + tag name.
    expect(
      messages.success({
        status: 200,
        data: { tag: "mood:chill", requested: 2, tagged: 2 },
      }),
    ).toBe("Tagged 2 tracks with mood:chill");
  });
});
