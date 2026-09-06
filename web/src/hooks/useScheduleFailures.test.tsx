import { QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import type { ScheduleListItem } from "#/api/generated/model";
import { createTestQueryClient } from "#/test/query-utils";
import { server } from "#/test/setup";

import { useScheduleFailures } from "./useScheduleFailures";

function schedule(overrides: Partial<ScheduleListItem>): ScheduleListItem {
  return {
    id: "sched-1",
    target_type: "sync",
    workflow_id: null,
    sync_target: "lastfm:plays",
    schedule_type: "daily",
    hour: 3,
    minute: 0,
    day_of_week: null,
    interval_minutes: null,
    timezone: "UTC",
    status: "enabled",
    next_run_at: null,
    last_run_at: null,
    last_run_status: null,
    last_error: null,
    consecutive_failures: 0,
    run_count: 4,
    target_label: "Last.fm plays",
    ...overrides,
  };
}

function mockSchedules(rows: ScheduleListItem[]) {
  server.use(
    http.get("*/api/v1/schedules", () => HttpResponse.json({ data: rows })),
  );
}

function wrapper() {
  const queryClient = createTestQueryClient();
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}

describe("useScheduleFailures", () => {
  it("keeps only the enabled schedules with a failure streak", async () => {
    mockSchedules([
      schedule({ id: "healthy", consecutive_failures: 0 }),
      schedule({ id: "broken", consecutive_failures: 3 }),
      // Paused: a schedule nobody is running can't be currently broken.
      schedule({ id: "paused", status: "disabled", consecutive_failures: 5 }),
    ]);

    const { result } = renderHook(() => useScheduleFailures(), {
      wrapper: wrapper(),
    });

    await waitFor(() => expect(result.current.count).toBe(1));
    expect(result.current.failing[0]?.id).toBe("broken");
  });

  it("holds one array reference while the data is unchanged", async () => {
    mockSchedules([schedule({ id: "broken", consecutive_failures: 2 })]);

    const { result, rerender } = renderHook(() => useScheduleFailures(), {
      wrapper: wrapper(),
    });

    await waitFor(() => expect(result.current.count).toBe(1));
    const first = result.current.failing;
    rerender();
    expect(result.current.failing).toBe(first);
  });

  it("reports no failures before the list has loaded", () => {
    mockSchedules([]);

    const { result } = renderHook(() => useScheduleFailures(), {
      wrapper: wrapper(),
    });

    expect(result.current.count).toBe(0);
    expect(result.current.failing).toHaveLength(0);
  });
});
