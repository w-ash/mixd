import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { ScheduleResponse } from "#/api/generated/model";

import { SchedulePicker } from "./SchedulePicker";

function makeSchedule(over: Partial<ScheduleResponse> = {}): ScheduleResponse {
  return {
    id: "00000000-0000-0000-0000-000000000001",
    target_type: "workflow",
    workflow_id: "00000000-0000-0000-0000-0000000000aa",
    sync_target: null,
    schedule_type: "daily",
    hour: 6,
    minute: 30,
    day_of_week: null,
    timezone: "UTC",
    status: "enabled",
    next_run_at: "2026-06-02T06:30:00Z",
    last_run_at: null,
    last_run_status: null,
    last_error: null,
    consecutive_failures: 0,
    run_count: 0,
    ...over,
  };
}

const noop = () => {};

describe("SchedulePicker", () => {
  it("shows a plain-English summary for an existing schedule", () => {
    render(
      <SchedulePicker
        schedule={makeSchedule()}
        onSave={noop}
        onToggle={noop}
        onRemove={noop}
      />,
    );
    expect(screen.getByText("Daily at 6:30 AM (UTC)")).toBeInTheDocument();
    expect(screen.getByText(/Next run:/)).toBeInTheDocument();
  });

  it("calls onToggle when the enabled switch is flipped", async () => {
    const onToggle = vi.fn();
    render(
      <SchedulePicker
        schedule={makeSchedule({ status: "enabled" })}
        onSave={noop}
        onToggle={onToggle}
        onRemove={noop}
      />,
    );
    await userEvent.click(screen.getByRole("switch", { name: "Enabled" }));
    expect(onToggle).toHaveBeenCalledWith(false);
  });

  it("calls onRemove from the summary view", async () => {
    const onRemove = vi.fn();
    render(
      <SchedulePicker
        schedule={makeSchedule()}
        onSave={noop}
        onToggle={noop}
        onRemove={onRemove}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /Remove/ }));
    expect(onRemove).toHaveBeenCalledOnce();
  });

  it("shows the form and saves defaults when no schedule exists", async () => {
    const onSave = vi.fn();
    render(
      <SchedulePicker
        schedule={null}
        onSave={onSave}
        onToggle={noop}
        onRemove={noop}
      />,
    );
    // Editor is shown immediately (no summary).
    expect(screen.getByLabelText("Time of day")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Schedule" }));
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        schedule_type: "daily",
        hour: 6,
        minute: 30,
        day_of_week: null,
      }),
    );
  });

  it("reveals the editor from the summary view", async () => {
    render(
      <SchedulePicker
        schedule={makeSchedule()}
        onSave={noop}
        onToggle={noop}
        onRemove={noop}
      />,
    );
    expect(screen.queryByLabelText("Time of day")).not.toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Edit schedule" }),
    );
    expect(screen.getByLabelText("Time of day")).toBeInTheDocument();
  });

  it("re-saves a weekly schedule with its day_of_week intact", async () => {
    // The cadence/day Radix Selects don't drive cleanly under jsdom (missing
    // hasPointerCapture), so seed an existing weekly schedule and re-save: the
    // editor seeds from the prop, exercising handleSave's `weekly ? dow : null`
    // branch without touching the popover. Weekly must carry day_of_week.
    const onSave = vi.fn();
    render(
      <SchedulePicker
        schedule={makeSchedule({
          schedule_type: "weekly",
          day_of_week: 2,
          hour: 18,
          minute: 0,
        })}
        onSave={onSave}
        onToggle={noop}
        onRemove={noop}
      />,
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Edit schedule" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Update schedule" }),
    );
    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        schedule_type: "weekly",
        day_of_week: 2,
        hour: 18,
        minute: 0,
      }),
    );
  });
});
