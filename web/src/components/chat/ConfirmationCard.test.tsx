import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ConfirmationCard } from "./ConfirmationCard";

const BASE_PROPS = {
  actionId: "abc-123",
  description: 'Create playlist "Friday Mix" with 24 tracks',
  details: {
    name: "Friday Mix",
    track_count: 24,
    source: "liked tracks",
  },
  toolName: "create_playlist",
  onConfirm: vi.fn(),
  onCancel: vi.fn(),
};

describe("ConfirmationCard", () => {
  it("renders the description and enabled buttons in the pending state", () => {
    render(<ConfirmationCard {...BASE_PROPS} state="pending" />);
    expect(screen.getByText(BASE_PROPS.description)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeEnabled();
  });

  it("renders the proposal details through the generic card", () => {
    render(<ConfirmationCard {...BASE_PROPS} state="pending" />);
    expect(screen.getByText("track count")).toBeInTheDocument();
    expect(screen.getByText("24")).toBeInTheDocument();
  });

  it("renders standard `changes` lines as a list when present", () => {
    render(
      <ConfirmationCard
        {...BASE_PROPS}
        state="pending"
        details={{
          operation: "rename",
          changes: ["Rename tag 'chill' → 'mellow'", "Affects 42 tracks"],
          tag_id: "tag-9",
        }}
      />,
    );
    expect(
      screen.getByText("Rename tag 'chill' → 'mellow'"),
    ).toBeInTheDocument();
    expect(screen.getByText("Affects 42 tracks")).toBeInTheDocument();
    // Raw commit params are not dumped when `changes` drives the display.
    expect(screen.queryByText("tag id")).not.toBeInTheDocument();
    expect(screen.queryByText("tag-9")).not.toBeInTheDocument();
  });

  it("renders the warning banner distinctly for destructive severity", () => {
    render(
      <ConfirmationCard
        {...BASE_PROPS}
        state="pending"
        details={{
          operation: "delete",
          severity: "destructive",
          warning: "This permanently deletes the tag and cannot be undone.",
          changes: ["Delete tag 'archive'", "Affects 8 tracks"],
          tag_id: "tag-3",
        }}
      />,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(
      "This permanently deletes the tag and cannot be undone.",
    );
    // Changes still render alongside the warning.
    expect(screen.getByText("Delete tag 'archive'")).toBeInTheDocument();
  });

  it("renders a soft (non-destructive) warning in a muted banner", () => {
    // A `warning` without `severity: "destructive"` still surfaces — e.g. the
    // delete-link / delete-assignment dispatchers attach a soft caveat. It must
    // reach the user, just without the red destructive treatment.
    render(
      <ConfirmationCard
        {...BASE_PROPS}
        state="pending"
        details={{
          operation: "delete",
          changes: ["Delete sync link link-7"],
          warning:
            "removes the sync link; the playlist and connector data stay intact",
        }}
      />,
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(
      "removes the sync link; the playlist and connector data stay intact",
    );
    // Muted styling, not the destructive red treatment.
    expect(alert.className).toContain("text-text-muted");
    expect(alert.className).not.toContain("text-destructive");
  });

  it("does not render a warning banner when no warning is present", () => {
    // BASE_PROPS.details carries no `warning` key.
    render(<ConfirmationCard {...BASE_PROPS} state="pending" />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("falls back to the generic display when `changes` is absent", () => {
    // BASE_PROPS.details has no `changes` key.
    render(<ConfirmationCard {...BASE_PROPS} state="pending" />);
    expect(screen.getByText("track count")).toBeInTheDocument();
    expect(screen.getByText("Friday Mix")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("calls onConfirm with the actionId when Confirm is clicked", async () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmationCard
        {...BASE_PROPS}
        state="pending"
        onConfirm={onConfirm}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(onConfirm).toHaveBeenCalledWith("abc-123");
  });

  it("calls onCancel with the actionId when Cancel is clicked", async () => {
    const onCancel = vi.fn();
    render(
      <ConfirmationCard {...BASE_PROPS} state="pending" onCancel={onCancel} />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledWith("abc-123");
  });

  it("disables both buttons in the loading state", () => {
    render(<ConfirmationCard {...BASE_PROPS} state="loading" />);
    expect(screen.getByRole("button", { name: /confirm/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
  });

  it("shows the Confirmed label in the confirmed state", () => {
    render(<ConfirmationCard {...BASE_PROPS} state="confirmed" />);
    expect(screen.getByText("Confirmed")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Confirm" }),
    ).not.toBeInTheDocument();
  });

  it("shows the Cancelled label in the cancelled state", () => {
    render(<ConfirmationCard {...BASE_PROPS} state="cancelled" />);
    expect(screen.getByText("Cancelled")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Confirm" }),
    ).not.toBeInTheDocument();
  });
});
