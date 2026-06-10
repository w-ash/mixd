import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import type { ScheduleResponse } from "#/api/generated/model";
import { ScheduleCard } from "#/components/shared/ScheduleCard";
import { useSyncScheduleController } from "#/hooks/useScheduleController";
import { server } from "#/test/setup";
import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
} from "#/test/test-utils";

const noop = () => {};

describe("ScheduleCard (presentational shell)", () => {
  it("shows a skeleton while loading, not the picker", () => {
    renderWithProviders(
      <ScheduleCard
        schedule={null}
        isLoading
        isPending={false}
        onSave={noop}
        onToggle={noop}
        onRemove={noop}
      />,
    );
    expect(screen.queryByLabelText("Time of day")).not.toBeInTheDocument();
  });

  it("renders the picker once loaded", () => {
    renderWithProviders(
      <ScheduleCard
        schedule={null}
        isLoading={false}
        isPending={false}
        onSave={noop}
        onToggle={noop}
        onRemove={noop}
      />,
    );
    expect(screen.getByLabelText("Time of day")).toBeInTheDocument();
  });
});

// Tiny consumer mirroring the sync card on the Sync page — proves the sync
// binding fetches/saves against the sync-schedule endpoints.
function SyncHarness({ targetId }: { targetId: string }) {
  return <ScheduleCard {...useSyncScheduleController(targetId)} />;
}

describe("useSyncScheduleController", () => {
  it("creates a sync schedule against /sync/schedules/{target}", async () => {
    // No existing schedule (404) → the picker opens in create mode.
    server.use(
      http.get("*/api/v1/sync/schedules/*", () =>
        HttpResponse.json({ detail: "not found" }, { status: 404 }),
      ),
    );

    let putUrl = "";
    let putBody: Record<string, unknown> = {};
    server.use(
      http.put("*/api/v1/sync/schedules/*", async ({ request }) => {
        putUrl = request.url;
        putBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          id: "1",
          target_type: "sync",
          sync_target: "spotify:likes",
        } as Partial<ScheduleResponse>);
      }),
    );

    renderWithProviders(<SyncHarness targetId="spotify:likes" />);

    // Create form appears once the 404 settles.
    await waitFor(() =>
      expect(screen.getByLabelText("Time of day")).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByRole("button", { name: "Schedule" }));

    await waitFor(() =>
      expect(putUrl).toContain("/sync/schedules/spotify:likes"),
    );
    expect(putBody).toMatchObject({
      schedule_type: "daily",
      hour: 6,
      minute: 30,
    });
  });
});
