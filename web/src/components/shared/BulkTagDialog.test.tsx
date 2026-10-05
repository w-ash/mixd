import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { Toaster } from "#/components/ui/sonner";
import { server } from "#/test/setup";
import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
} from "#/test/test-utils";

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
    <>
      <BulkTagDialog
        open={true}
        onOpenChange={onOpenChange}
        trackIds={trackIds}
        onTagged={onTagged}
        {...overrides}
      />
      <Toaster />
    </>,
  );
  return { onOpenChange, onTagged };
}

async function pickTagAndConfirm(tag: string) {
  await userEvent.type(screen.getByPlaceholderText("Pick or add a tag…"), tag);
  await userEvent.click(await screen.findByText(tag));
  await userEvent.click(screen.getByRole("button", { name: "Tag 2 tracks" }));
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

  it("sends the chosen tag for every selected track, then reports success and closes", async () => {
    const bodies: unknown[] = [];
    server.use(
      http.post("*/api/v1/tracks/tags/batch", async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({
          tag: "mood:chill",
          requested: 2,
          tagged: 2,
        });
      }),
    );

    const { onOpenChange, onTagged } = setup();
    await pickTagAndConfirm("mood:chill");

    await waitFor(() => {
      expect(onTagged).toHaveBeenCalledOnce();
    });
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(bodies).toEqual([{ track_ids: trackIds, tag: "mood:chill" }]);
  });

  it("disables the confirm button until a tag is chosen", async () => {
    setup();
    const btn = await screen.findByRole("button", { name: "Tag 2 tracks" });
    expect(btn).toBeDisabled();
  });

  it("closes on cancel", async () => {
    const { onOpenChange } = setup();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("toasts progress while tagging, then the tagged count and tag", async () => {
    let release: () => void = () => {};
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    server.use(
      http.post("*/api/v1/tracks/tags/batch", async () => {
        await held;
        return HttpResponse.json({
          tag: "mood:chill",
          requested: 2,
          tagged: 2,
        });
      }),
    );

    setup();
    await pickTagAndConfirm("mood:chill");

    expect(await screen.findByText("Tagging 2 tracks…")).toBeInTheDocument();
    release();
    expect(
      await screen.findByText("Tagged 2 tracks with mood:chill"),
    ).toBeInTheDocument();
  });

  it("toasts the failure and keeps the dialog open when tagging fails", async () => {
    server.use(
      http.post("*/api/v1/tracks/tags/batch", () =>
        HttpResponse.json(
          { error: { code: "INTERNAL", message: "boom" } },
          { status: 500 },
        ),
      ),
    );

    const { onOpenChange, onTagged } = setup();
    await pickTagAndConfirm("mood:chill");

    expect(await screen.findByText("Failed to tag tracks")).toBeInTheDocument();
    expect(onTagged).not.toHaveBeenCalled();
    expect(onOpenChange).not.toHaveBeenCalled();
  });
});
