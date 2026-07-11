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
