import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import type {
  ScheduleListItem,
  ScheduleListResponse,
} from "#/api/generated/model";
import { server } from "#/test/setup";
import { renderWithProviders, screen, waitFor } from "#/test/test-utils";

import { ScheduleFailuresBanner } from "./ScheduleFailuresBanner";

function makeSchedule(over: Partial<ScheduleListItem> = {}): ScheduleListItem {
  return {
    id: "00000000-0000-0000-0000-000000000001",
    target_type: "sync",
    workflow_id: null,
    sync_target: "spotify:likes",
    target_label: "Spotify likes",
    schedule_type: "daily",
    hour: 3,
    minute: 0,
    day_of_week: null,
    timezone: "UTC",
    status: "enabled",
    next_run_at: "2026-06-08T03:00:00Z",
    last_run_at: "2026-06-07T03:00:00Z",
    last_run_status: "failed",
    last_error: "SpotifyAuthError",
    consecutive_failures: 0,
    run_count: 5,
    ...over,
  };
}

function mockSchedules(rows: ScheduleListItem[]) {
  server.use(
    http.get("*/api/v1/schedules", () =>
      HttpResponse.json({ data: rows } satisfies ScheduleListResponse),
    ),
  );
}

describe("ScheduleFailuresBanner", () => {
  it("counts the failing schedules and names them", async () => {
    mockSchedules([
      makeSchedule({
        id: "a",
        sync_target: "spotify:likes",
        consecutive_failures: 2,
      }),
      makeSchedule({
        id: "b",
        target_type: "workflow",
        sync_target: null,
        workflow_id: "00000000-0000-0000-0000-0000000000ff",
        target_label: "Fresh Faves",
        consecutive_failures: 1,
      }),
      makeSchedule({
        id: "c",
        sync_target: "lastfm:plays",
        target_label: "Last.fm plays",
        consecutive_failures: 0,
      }),
      // Disabled-but-previously-failed: must NOT count — a paused schedule can't
      // run to clear its streak, so counting it would pin the banner open.
      makeSchedule({
        id: "d",
        status: "disabled",
        sync_target: "lastfm:likes",
        target_label: "Last.fm loves",
        consecutive_failures: 9,
      }),
    ]);
    renderWithProviders(<ScheduleFailuresBanner />);

    // Two of the four schedules are actively (enabled) failing.
    await waitFor(() =>
      expect(screen.getByText("2 scheduled runs failing")).toBeInTheDocument(),
    );
    // Each failing item is named (server-resolved label) and links to its home.
    const syncLink = screen.getByRole("link", { name: "Spotify likes" });
    expect(syncLink).toHaveAttribute("href", "/settings/sync");
    const workflowLink = screen.getByRole("link", { name: "Fresh Faves" });
    expect(workflowLink).toHaveAttribute(
      "href",
      "/workflows/00000000-0000-0000-0000-0000000000ff",
    );
  });

  it("renders nothing when every schedule is healthy", async () => {
    let fetched = false;
    server.use(
      http.get("*/api/v1/schedules", () => {
        fetched = true;
        return HttpResponse.json({
          data: [makeSchedule({ consecutive_failures: 0 })],
        } satisfies ScheduleListResponse);
      }),
    );
    const { container } = renderWithProviders(<ScheduleFailuresBanner />);

    // Wait for the healthy list to actually be consumed, then assert it
    // produced no banner (not just an unresolved query rendering null).
    await waitFor(() => expect(fetched).toBe(true));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(container).toBeEmptyDOMElement();
  });

  it("uses singular wording for a single failing run", async () => {
    mockSchedules([makeSchedule({ consecutive_failures: 1 })]);
    renderWithProviders(<ScheduleFailuresBanner />);

    await waitFor(() =>
      expect(screen.getByText("1 scheduled run failing")).toBeInTheDocument(),
    );
  });
});
