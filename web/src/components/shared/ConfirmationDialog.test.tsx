import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";

import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
} from "@/test/test-utils";

import { ConfirmationDialog } from "./ConfirmationDialog";

const defaultProps: ComponentProps<typeof ConfirmationDialog> = {
  open: true,
  onOpenChange: vi.fn(),
  title: "Confirm Action",
  confirmLabel: "Do it",
  onConfirm: vi.fn(),
};

function renderDialog(
  props: Partial<ComponentProps<typeof ConfirmationDialog>> = {},
) {
  return renderWithProviders(
    <ConfirmationDialog {...defaultProps} {...props} />,
  );
}

describe("ConfirmationDialog", () => {
  it("renders title and confirm button when open", async () => {
    renderDialog();

    await waitFor(() => {
      expect(screen.getByText("Confirm Action")).toBeInTheDocument();
    });

    expect(screen.getByRole("button", { name: "Do it" })).toBeInTheDocument();
  });

  it("does not render content when closed", () => {
    renderDialog({ open: false });

    expect(screen.queryByText("Confirm Action")).not.toBeInTheDocument();
  });

  it("renders description when provided", async () => {
    renderDialog({ description: "This will affect all tracks." });

    await waitFor(() => {
      expect(screen.getByText("Confirm Action")).toBeInTheDocument();
    });

    expect(
      screen.getByText("This will affect all tracks."),
    ).toBeInTheDocument();
  });

  it("renders children content", async () => {
    renderWithProviders(
      <ConfirmationDialog {...defaultProps}>
        <p>Preview content here</p>
      </ConfirmationDialog>,
    );

    await waitFor(() => {
      expect(screen.getByText("Confirm Action")).toBeInTheDocument();
    });

    expect(screen.getByText("Preview content here")).toBeInTheDocument();
  });

  it("renders cancel button with default label", async () => {
    renderDialog();

    await waitFor(() => {
      expect(screen.getByText("Confirm Action")).toBeInTheDocument();
    });

    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  });

  it("renders custom cancel label", async () => {
    renderDialog({ cancelLabel: "Never mind" });

    await waitFor(() => {
      expect(screen.getByText("Confirm Action")).toBeInTheDocument();
    });

    expect(
      screen.getByRole("button", { name: "Never mind" }),
    ).toBeInTheDocument();
  });

  it("calls onConfirm when confirm button is clicked", async () => {
    const onConfirm = vi.fn();
    renderDialog({ onConfirm });

    await waitFor(() => {
      expect(screen.getByText("Confirm Action")).toBeInTheDocument();
    });

    await userEvent.click(screen.getByRole("button", { name: "Do it" }));
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it("calls onOpenChange(false) when cancel button is clicked", async () => {
    const onOpenChange = vi.fn();
    renderDialog({ onOpenChange });

    await waitFor(() => {
      expect(screen.getByText("Confirm Action")).toBeInTheDocument();
    });

    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("disables confirm button when isPending is true", async () => {
    renderDialog({ isPending: true });

    await waitFor(() => {
      expect(screen.getByText("Confirm Action")).toBeInTheDocument();
    });

    expect(screen.getByRole("button", { name: "Do it" })).toBeDisabled();
  });

  it("disables confirm button when disabled is true", async () => {
    renderDialog({ disabled: true });

    await waitFor(() => {
      expect(screen.getByText("Confirm Action")).toBeInTheDocument();
    });

    expect(screen.getByRole("button", { name: "Do it" })).toBeDisabled();
  });

  it("shows spinner icon when isPending", async () => {
    renderDialog({ isPending: true });

    await waitFor(() => {
      expect(screen.getByText("Confirm Action")).toBeInTheDocument();
    });

    // Loader2 renders an SVG with animate-spin class (portaled to document.body)
    expect(document.body.querySelector(".animate-spin")).toBeInTheDocument();
  });

  it("does not show spinner when not pending", async () => {
    renderDialog({ isPending: false });

    await waitFor(() => {
      expect(screen.getByText("Confirm Action")).toBeInTheDocument();
    });

    expect(
      document.body.querySelector(".animate-spin"),
    ).not.toBeInTheDocument();
  });
});
