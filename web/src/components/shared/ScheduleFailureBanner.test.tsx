import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ScheduleFailureBanner } from "./ScheduleFailureBanner";

describe("ScheduleFailureBanner", () => {
  it("renders nothing when there are no consecutive failures", () => {
    const { container } = render(
      <ScheduleFailureBanner
        schedule={{ consecutive_failures: 0, last_error: null }}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows the failure count and sanitized last error", () => {
    render(
      <ScheduleFailureBanner
        schedule={{ consecutive_failures: 3, last_error: "SpotifyAuthError" }}
      />,
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(
      screen.getByText("3 consecutive scheduled runs failed"),
    ).toBeInTheDocument();
    expect(screen.getByText("SpotifyAuthError")).toBeInTheDocument();
  });

  it("uses singular wording for a single failure", () => {
    render(
      <ScheduleFailureBanner
        schedule={{ consecutive_failures: 1, last_error: null }}
      />,
    );
    expect(
      screen.getByText("1 consecutive scheduled run failed"),
    ).toBeInTheDocument();
  });
});
