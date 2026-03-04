import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { FileUpload } from "./FileUpload";

describe("FileUpload", () => {
  it("renders choose file button", () => {
    render(<FileUpload onFileSelect={vi.fn()} />);

    expect(
      screen.getByRole("button", { name: /choose file/i }),
    ).toBeInTheDocument();
  });

  it("calls onFileSelect when a file is chosen", async () => {
    const onFileSelect = vi.fn();
    const user = userEvent.setup();
    render(<FileUpload onFileSelect={onFileSelect} />);

    const file = new File(["{}"], "data.json", { type: "application/json" });
    const input = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    await user.upload(input, file);

    expect(onFileSelect).toHaveBeenCalledWith(file);
  });

  it("shows selected filename after selection", async () => {
    const user = userEvent.setup();
    render(<FileUpload onFileSelect={vi.fn()} />);

    const file = new File(["{}"], "history.json", {
      type: "application/json",
    });
    const input = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    await user.upload(input, file);

    expect(screen.getByText(/history\.json/)).toBeInTheDocument();
  });

  it("shows error for oversized files", async () => {
    const user = userEvent.setup();
    render(<FileUpload onFileSelect={vi.fn()} maxSize={100} />);

    // Create a file larger than 100 bytes
    const content = "x".repeat(200);
    const file = new File([content], "big.json", {
      type: "application/json",
    });
    const input = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    await user.upload(input, file);

    expect(screen.getByRole("alert")).toHaveTextContent(/file too large/i);
  });

  it("disables button when disabled prop is true", () => {
    render(<FileUpload onFileSelect={vi.fn()} disabled />);

    expect(screen.getByRole("button", { name: /choose file/i })).toBeDisabled();
  });
});
