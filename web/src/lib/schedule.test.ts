import { describe, expect, it } from "vitest";

import {
  describeSchedule,
  formatClockTime,
  isScheduleFailing,
} from "./schedule";

describe("formatClockTime", () => {
  it("formats morning times in 12-hour clock", () => {
    expect(formatClockTime(6, 30)).toBe("6:30 AM");
    expect(formatClockTime(0, 5)).toBe("12:05 AM");
  });

  it("formats afternoon/evening times", () => {
    expect(formatClockTime(13, 0)).toBe("1:00 PM");
    expect(formatClockTime(12, 0)).toBe("12:00 PM");
    expect(formatClockTime(23, 59)).toBe("11:59 PM");
  });
});

describe("describeSchedule", () => {
  it("describes a daily schedule", () => {
    expect(
      describeSchedule({
        schedule_type: "daily",
        hour: 6,
        minute: 30,
        day_of_week: null,
        timezone: "UTC",
      }),
    ).toBe("Daily at 6:30 AM (UTC)");
  });

  it("describes a weekly schedule with the weekday name", () => {
    expect(
      describeSchedule({
        schedule_type: "weekly",
        hour: 18,
        minute: 0,
        day_of_week: 0,
        timezone: "America/Los_Angeles",
      }),
    ).toBe("Weekly on Sunday at 6:00 PM (America/Los_Angeles)");
  });
});

describe("isScheduleFailing", () => {
  it("is true for an enabled schedule with a non-zero streak", () => {
    expect(
      isScheduleFailing({ status: "enabled", consecutive_failures: 2 }),
    ).toBe(true);
  });

  it("is false for an enabled schedule with no failures", () => {
    expect(
      isScheduleFailing({ status: "enabled", consecutive_failures: 0 }),
    ).toBe(false);
  });

  it("is false for a disabled schedule even with a stale streak", () => {
    // A paused schedule can never run to reset the streak — counting it would
    // pin the dashboard banner / row marker open forever.
    expect(
      isScheduleFailing({ status: "disabled", consecutive_failures: 5 }),
    ).toBe(false);
  });
});
